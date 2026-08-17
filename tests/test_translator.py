import threading

import pytest

ML = (0x0D00, 0x0D7F)   # Malayalam unicode block
TE = (0x0C00, 0x0C7F)   # Telugu
HI = (0x0900, 0x097F)   # Devanagari


def has_script(text, block):
    lo, hi = block
    return any(lo <= ord(ch) <= hi for ch in text)


@pytest.fixture(scope="module")
def translator():
    from translator import load_translator
    return load_translator()


@pytest.mark.slow
async def test_translates_into_three_scripts(translator):
    out = await translator.translate_all("God is love and He loves the world.")
    assert set(out.keys()) == {"ml", "te", "hi"}
    assert has_script(out["ml"], ML), f"not Malayalam: {out['ml']}"
    assert has_script(out["te"], TE), f"not Telugu: {out['te']}"
    assert has_script(out["hi"], HI), f"not Hindi: {out['hi']}"


@pytest.mark.slow
async def test_empty_text_returns_empties(translator):
    out = await translator.translate_all("")
    assert out == {"ml": "", "te": "", "hi": ""}


# --- Fast tests: real IndicProcessor, fake model/tokenizer (no GPU, no download) ---

# What IndicTrans2 actually emits before postprocessing: Devanagari-ish text that
# postprocess_batch(lang=...) transliterates into each target script.
DECODED = [
    "दैवं स्नेहमाण्, अवൻ लोकत्तॆ स्नेहिक्कुन्नु.",      # -> Malayalam
    "देवुडु प्रेम, आयन प्रपंचान्नि प्रेमिस्ताडु.",        # -> Telugu
    "ईश्वर प्रेम है और वह संसार से प्रेम करता है ।",   # -> Hindi
]


class _FakeEncoding(dict):
    """Stands in for a BatchEncoding: dict-like, and .to() is a no-op."""

    def to(self, device):
        return self


class _FakeTokenizer:
    def __init__(self):
        self.seen_batch = None

    def __call__(self, batch, **kwargs):
        self.seen_batch = list(batch)
        return _FakeEncoding(input_ids=[[1, 2]] * len(batch))

    def batch_decode(self, out, **kwargs):
        return list(out)


class _FakeModel:
    def __init__(self):
        self.generate_calls = 0

    def generate(self, **kwargs):
        self.generate_calls += 1
        return list(DECODED)


def _translator_with_fake_model():
    """An IndicTrans2Translator with real processors but no model weights."""
    import translator as T

    t = object.__new__(T.IndicTrans2Translator)
    t.device = "cpu"
    t.tokenizer = _FakeTokenizer()
    t.model = _FakeModel()
    t.processors = T._make_processors()
    return t


def _run_with_timeout(fn, timeout=30):
    """Run fn in a thread so a placeholder-queue deadlock fails instead of hanging."""
    box = {}
    done = threading.Event()

    def target():
        try:
            box["out"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            box["err"] = exc
        finally:
            done.set()

    threading.Thread(target=target, daemon=True).start()
    finished = done.wait(timeout)
    if "err" in box:
        raise box["err"]
    assert finished, (
        f"translate_all_sync did not return within {timeout}s — a shared "
        "IndicProcessor drains the placeholder-map queue and blocks on get()"
    )
    return box["out"]


def test_translate_all_sync_postprocesses_every_language():
    """Regression: each language needs its own IndicProcessor.

    postprocess_batch() pops one placeholder-entity map per sentence and then
    clears the queue, so a single shared processor loses the maps belonging to
    the remaining languages and the next call blocks forever.
    """
    t = _translator_with_fake_model()
    out = _run_with_timeout(lambda: t.translate_all_sync("God is love and He loves the world."))

    assert set(out.keys()) == {"ml", "te", "hi"}
    assert has_script(out["ml"], ML), f"not Malayalam: {out['ml']}"
    assert has_script(out["te"], TE), f"not Telugu: {out['te']}"
    assert has_script(out["hi"], HI), f"not Hindi: {out['hi']}"


def test_all_languages_share_one_generate_call():
    """The latency design depends on one batched generate for all targets."""
    t = _translator_with_fake_model()
    _run_with_timeout(lambda: t.translate_all_sync("God is love and He loves the world."))

    assert t.model.generate_calls == 1
    assert len(t.tokenizer.seen_batch) == 3
    for tag, sent in zip(("mal_Mlym", "tel_Telu", "hin_Deva"), t.tokenizer.seen_batch):
        assert sent.startswith(f"eng_Latn {tag}"), sent


async def test_empty_text_short_circuits_without_model():
    """Empty input never reaches the GPU."""
    t = _translator_with_fake_model()
    import asyncio

    t._lock = asyncio.Lock()

    assert await t.translate_all("   ") == {"ml": "", "te": "", "hi": ""}
    assert t.model.generate_calls == 0
