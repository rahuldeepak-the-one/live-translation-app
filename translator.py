"""Translation backends: IndicTrans2 (primary) and NLLB (fallback).

Both expose:  async translate_all(text) -> {"ml": ..., "te": ..., "hi": ...}
IndicTrans2 translates all targets in ONE batched generate call.
"""
import asyncio
import logging
import re

import torch

from config import (
    TARGET_LANGS, FLORES_CODES, INDICTRANS2_MODEL, NLLB_MODEL, TRANSLATOR_BACKEND,
    MT_NUM_BEAMS, MT_NO_REPEAT_NGRAM, MT_REPETITION_PENALTY, GLOSSARY,
    MT_USE_CONTEXT, SOURCE_REWRITES,
)

logger = logging.getLogger(__name__)


# IndicTrans2 emits three different encodings of the same Malayalam nṟa
# conjunct. The 2026-08-21 session carried legacy U+0D7B U+0D31 (11 times) and
# U+0D7B U+0D4D U+0D31 (2) alongside the standard form (3); the first two render
# inconsistently on phones and in Android WebView. U+0D4C is likewise deprecated
# in favour of U+0D57. Order matters: the longer sequence is rewritten first.
_ML_REWRITES = (
    ("\u0d7b\u0d4d\u0d31", "\u0d28\u0d4d\u0d31"),   # ൻ്റ -> ന്റ
    ("\u0d7b\u0d31", "\u0d28\u0d4d\u0d31"),          # ൻറ  -> ന്റ
    ("\u0d4c", "\u0d57"),                             # ൌ   -> ൗ
)


def normalize_malayalam(text):
    """Rewrite legacy Malayalam codepoints to their standard equivalents."""
    for old, new in _ML_REWRITES:
        text = text.replace(old, new)
    return text


# Per-language output cleanup, applied after postprocess_batch().
_POST_NORMALIZE = {"ml": normalize_malayalam}


def _postprocess(lang, text):
    normalize = _POST_NORMALIZE.get(lang)
    return normalize(text) if normalize else text


def apply_source_rewrites(text):
    """Rewrite English the model reliably mistranslates, before it is sent.

    Case-insensitive: the target scripts have no case and this only ever
    touches the translator's input. See config.SOURCE_REWRITES for the measured
    justification of each row.
    """
    for pattern, replacement in SOURCE_REWRITES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def split_source_sentences(text):
    """Split English into sentences for independent translation."""
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]


def apply_glossary(lang, text):
    """Pin agreed terminology. Stem-level, so inflectional suffixes survive.

    Longest key first: a stem that is a prefix of another must not consume it.
    See config.GLOSSARY for the format and why it ships empty.
    """
    terms = GLOSSARY.get(lang) or {}
    for variant in sorted(terms, key=len, reverse=True):
        text = text.replace(variant, terms[variant])
    return text


# Sentence enders as they appear in TARGET text. Hindi output from IndicTrans2
# ends on U+0964 DEVANAGARI DANDA, not a full stop — splitting on "." alone
# would treat a whole Hindi paragraph as a single sentence and the
# context-recovery step below would return nothing.
_TARGET_ENDERS = ".\u0964?!"


def split_target_sentences(text):
    """Split target text into sentences, discarding punctuation-only debris.

    The context's translation sometimes collapses to a bare full stop, and that
    debris was reaching the screens as a leading ". . ". Dropping it here also
    keeps the integrity guard honest: debris counted as a sentence would mask
    real content loss.
    """
    out, current = [], ""
    for ch in text:
        current += ch
        if ch in _TARGET_ENDERS:
            if re.search(r"\w", current):
                out.append(current.strip())
            current = ""
    if re.search(r"\w", current):
        out.append(current.strip())
    return out


def last_sentence(text):
    """The final English sentence of the context.

    Only one sentence of context is used: whole-segment context measurably
    contaminated the current sentence (2026-08-21 seg9, "Not the master's
    wealth" came back as "the steward's wealth decreased").
    """
    parts = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]
    return parts[-1] if parts else ""


def count_source_sentences(text):
    return max(1, sum(text.count(c) for c in ".?!"))


def _make_processors():
    """One IndicProcessor per target language.

    IndicProcessor stashes placeholder-entity maps in an internal Queue:
    preprocess_batch() pushes one map per sentence, postprocess_batch() pops one
    per sentence and then CLEARS the queue. Each target language needs its own
    postprocess_batch(lang=...) call — that call transliterates Devanagari into
    the target script — so with a single shared processor the first call would
    clear the maps belonging to the other languages and the next call would
    block forever on an empty queue. Per-language processors keep every push
    paired with its own pop.
    """
    try:
        from IndicTransToolkit.processor import IndicProcessor
    except ImportError:  # older package layout
        from IndicTransToolkit import IndicProcessor

    return {lang: IndicProcessor(inference=True) for lang in TARGET_LANGS}


class _AsyncTranslatorBase:
    """Serializes GPU access and keeps the event loop unblocked."""

    def __init__(self):
        self._lock = asyncio.Lock()

    async def translate_all(self, text, context=""):
        if not text or not text.strip():
            return {lang: "" for lang in TARGET_LANGS}
        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, self.translate_all_sync, text, context)

    async def translate_sentences(self, sentences, context=""):
        """One dict per sentence. Stage 2's per-sentence publishing path.

        Holds the GPU lock once for the whole list, not once per sentence —
        the batching inside translate_sentences_sync is only worth anything if
        the caller does not serialise around it.
        """
        if not sentences:
            return []
        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, self.translate_sentences_sync, sentences, context)


class IndicTrans2Translator(_AsyncTranslatorBase):
    def __init__(self, model_name=INDICTRANS2_MODEL):
        super().__init__()
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        logger.info("Loading IndicTrans2 (%s) on %s...", model_name, self.device)
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(
            model_name, trust_remote_code=True, torch_dtype=dtype
        ).to(self.device)
        self.model.eval()
        self.processors = _make_processors()
        logger.info("IndicTrans2 loaded.")

    def _generate(self, sentences, langs):
        """One batched generate: every sentence x every language, one call.

        Sentences are separate batch rows rather than one concatenated input,
        because inside a single input an earlier sentence bleeds into a later
        one — seg9 of 2026-08-21 rendered "Stewards" as masters/owners in te
        and hi purely because sentence 1 mentioned "the master". Each sentence
        alone is correct.

        preprocess_batch pushes one placeholder map per sentence and
        postprocess_batch pops one per sentence, so push N / pop N per language
        keeps the queue paired — see _make_processors() for why that matters.
        """
        if isinstance(sentences, str):
            sentences = [sentences]
        batch = []
        for lang in langs:
            batch.extend(
                self.processors[lang].preprocess_batch(
                    sentences, src_lang=FLORES_CODES["en"],
                    tgt_lang=FLORES_CODES[lang],
                )
            )
        inputs = self.tokenizer(
            batch, truncation=True, padding="longest",
            return_tensors="pt", max_length=256,
        ).to(self.device)

        with torch.no_grad():
            out = self.model.generate(
                **inputs, max_length=256, num_beams=MT_NUM_BEAMS,
                num_return_sequences=1,
                no_repeat_ngram_size=MT_NO_REPEAT_NGRAM,
                repetition_penalty=MT_REPETITION_PENALTY,
            )

        decoded = self.tokenizer.batch_decode(
            out, skip_special_tokens=True, clean_up_tokenization_spaces=True
        )
        n = len(sentences)
        # Per-sentence, NOT joined. The boundaries were always computed here and
        # thrown away by a join; Stage 2 publishes each sentence under its own
        # id and needs them. Callers that still want one string per language
        # join at their own call site — see _joined().
        result = {}
        for i, lang in enumerate(langs):
            raws = decoded[i * n:(i + 1) * n]
            outs = self.processors[lang].postprocess_batch(
                raws, lang=FLORES_CODES[lang]
            )
            result[lang] = [
                apply_glossary(lang, _postprocess(lang, o)) for o in outs
            ]
        return result

    def _joined(self, sentences, langs):
        """_generate, collapsed back to one string per language.

        The chunk-level API still wants a single string; Stage 2's per-sentence
        API does not. One generate call either way.
        """
        per_sentence = self._generate(sentences, langs)
        return {lang: " ".join(parts) for lang, parts in per_sentence.items()}

    def translate_sentences_sync(self, sentences, context=""):
        """Translate a list of sentences, returning one dict per sentence.

        Stage 2 publishes each sentence under its own id, so it needs the
        boundaries _generate already computes. Critically this is still ONE
        batched generate for every sentence x every language — turning a
        3-sentence chunk into three serialised GPU calls behind the asyncio
        lock would undo the latency design (mt median 0.8s per chunk on
        2026-08-21) for no benefit.
        """
        cleaned = [apply_source_rewrites(s) for s in sentences if s and s.strip()]
        if not cleaned:
            return []

        # Context mode (off by default — see config.MT_USE_CONTEXT, which
        # measured it inventing a negation) needs the single-input path and its
        # content-loss recovery, and only the FIRST sentence has a predecessor
        # outside this batch. So delegate that one and batch the rest, rather
        # than reimplementing the recovery here or silently dropping context.
        if MT_USE_CONTEXT and context:
            head = self.translate_all_sync(cleaned[0], context=context)
            if len(cleaned) == 1:
                return [head]
            rest = self._generate(cleaned[1:], TARGET_LANGS)
            return [head] + [
                {lang: rest[lang][i] for lang in TARGET_LANGS}
                for i in range(len(cleaned) - 1)
            ]

        per_lang = self._generate(cleaned, TARGET_LANGS)
        return [
            {lang: per_lang[lang][i] for lang in TARGET_LANGS}
            for i in range(len(cleaned))
        ]

    def translate_all_sync(self, text, context=""):
        text = apply_source_rewrites(text)
        ctx = last_sentence(context) if (MT_USE_CONTEXT and context) else ""
        if not ctx:
            # Per-sentence: independent rows, still one generate call.
            return self._joined(split_source_sentences(text) or [text],
                                TARGET_LANGS)

        # Context mode keeps the single-input path: the recovery step drops the
        # context's translation, which only makes sense on one joined output.
        joined = self._joined([f"{ctx} {text}"], TARGET_LANGS)

        # Recovery. ctx is always exactly one sentence, so exactly one target
        # sentence is dropped. If what is left has fewer sentences than the
        # source, the context ate real content — retry that language with no
        # context rather than ship a caption missing what the speaker said.
        expected = count_source_sentences(text)
        result, retry = {}, []
        for lang in TARGET_LANGS:
            kept = split_target_sentences(joined[lang])[1:]
            if len(kept) < expected:
                logger.info(
                    "context lost content for %s (%d of %d sentences) — retrying",
                    lang, len(kept), expected,
                )
                retry.append(lang)
            else:
                result[lang] = " ".join(kept)
        if retry:
            result.update(self._joined(split_source_sentences(text) or [text],
                                       retry))
        return {lang: result[lang] for lang in TARGET_LANGS}


class NLLBTranslator(_AsyncTranslatorBase):
    def __init__(self, model_name=NLLB_MODEL):
        super().__init__()
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Loading NLLB (%s) on %s...", model_name, self.device)
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_name).to(self.device)
        self.model.eval()
        logger.info("NLLB loaded.")

    def translate_all_sync(self, text, context=""):
        # The fallback ignores context: it exists to keep the service alive when
        # IndicTrans2 will not load, not to match its quality.
        text = apply_source_rewrites(text)
        result = {}
        for lang in TARGET_LANGS:
            self.tokenizer.src_lang = FLORES_CODES["en"]
            inputs = self.tokenizer(
                text, return_tensors="pt", truncation=True, max_length=512
            ).to(self.device)
            tgt_id = self.tokenizer.convert_tokens_to_ids(FLORES_CODES[lang])
            with torch.no_grad():
                out = self.model.generate(
                    **inputs, forced_bos_token_id=tgt_id, max_new_tokens=512,
                    no_repeat_ngram_size=MT_NO_REPEAT_NGRAM,
                    repetition_penalty=MT_REPETITION_PENALTY,
                )
            result[lang] = apply_glossary(lang, _postprocess(
                lang, self.tokenizer.batch_decode(out, skip_special_tokens=True)[0]
            ))
        return result


def load_translator():
    """Build the configured backend; fall back to NLLB if IndicTrans2 fails."""
    if TRANSLATOR_BACKEND == "indictrans2":
        try:
            return IndicTrans2Translator()
        except Exception:
            logger.exception("IndicTrans2 failed to load — falling back to NLLB")
    return NLLBTranslator()
