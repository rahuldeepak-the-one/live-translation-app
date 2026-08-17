"""Translation backends: IndicTrans2 (primary) and NLLB (fallback).

Both expose:  async translate_all(text) -> {"ml": ..., "te": ..., "hi": ...}
IndicTrans2 translates all targets in ONE batched generate call.
"""
import asyncio
import logging

import torch

from config import (
    TARGET_LANGS, FLORES_CODES, INDICTRANS2_MODEL, NLLB_MODEL, TRANSLATOR_BACKEND,
)

logger = logging.getLogger(__name__)


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

    async def translate_all(self, text):
        if not text or not text.strip():
            return {lang: "" for lang in TARGET_LANGS}
        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.translate_all_sync, text)


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

    def translate_all_sync(self, text):
        # Build one batch: the same sentence tagged for each target language.
        batch = []
        for lang in TARGET_LANGS:
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
                **inputs, max_length=256, num_beams=1, num_return_sequences=1
            )

        decoded = self.tokenizer.batch_decode(
            out, skip_special_tokens=True, clean_up_tokenization_spaces=True
        )
        result = {}
        for lang, raw in zip(TARGET_LANGS, decoded):
            result[lang] = self.processors[lang].postprocess_batch(
                [raw], lang=FLORES_CODES[lang]
            )[0]
        return result


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

    def translate_all_sync(self, text):
        result = {}
        for lang in TARGET_LANGS:
            self.tokenizer.src_lang = FLORES_CODES["en"]
            inputs = self.tokenizer(
                text, return_tensors="pt", truncation=True, max_length=512
            ).to(self.device)
            tgt_id = self.tokenizer.convert_tokens_to_ids(FLORES_CODES[lang])
            with torch.no_grad():
                out = self.model.generate(
                    **inputs, forced_bos_token_id=tgt_id, max_new_tokens=512
                )
            result[lang] = self.tokenizer.batch_decode(out, skip_special_tokens=True)[0]
        return result


def load_translator():
    """Build the configured backend; fall back to NLLB if IndicTrans2 fails."""
    if TRANSLATOR_BACKEND == "indictrans2":
        try:
            return IndicTrans2Translator()
        except Exception:
            logger.exception("IndicTrans2 failed to load — falling back to NLLB")
    return NLLBTranslator()
