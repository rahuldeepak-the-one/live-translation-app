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
    MT_USE_CONTEXT,
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

    def _generate(self, text, langs):
        """One batched generate for the given languages.

        Exactly one preprocess_batch push is paired with one postprocess_batch
        pop per language — see _make_processors() for why that matters.
        """
        batch = []
        for lang in langs:
            batch.extend(
                self.processors[lang].preprocess_batch(
                    [text], src_lang=FLORES_CODES["en"], tgt_lang=FLORES_CODES[lang]
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
        result = {}
        for lang, raw in zip(langs, decoded):
            text_out = self.processors[lang].postprocess_batch(
                [raw], lang=FLORES_CODES[lang]
            )[0]
            result[lang] = apply_glossary(lang, _postprocess(lang, text_out))
        return result

    def translate_all_sync(self, text, context=""):
        ctx = last_sentence(context) if (MT_USE_CONTEXT and context) else ""
        if not ctx:
            return self._generate(text, TARGET_LANGS)

        joined = self._generate(f"{ctx} {text}", TARGET_LANGS)

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
            result.update(self._generate(text, retry))
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
