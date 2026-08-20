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
    """decoded may be one list (reused) or a list of lists (one per call)."""

    def __init__(self, decoded=None):
        self.generate_calls = 0
        self.generate_kwargs = None
        d = DECODED if decoded is None else decoded
        self.per_call = d if d and isinstance(d[0], list) else None
        self.decoded = None if self.per_call else list(d)

    def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        i = self.generate_calls
        self.generate_calls += 1
        if self.per_call is not None:
            return list(self.per_call[min(i, len(self.per_call) - 1)])
        return list(self.decoded)


def _translator_with_fake_model(decoded=None):
    """An IndicTrans2Translator with real processors but no model weights."""
    import translator as T

    t = object.__new__(T.IndicTrans2Translator)
    t.device = "cpu"
    t.tokenizer = _FakeTokenizer()
    t.model = _FakeModel(decoded)
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


# --- Repetition control -----------------------------------------------------

def test_generate_guards_against_repetition_loops():
    """Beam search alone will happily emit one token to the length cap.

    Segment 3 of the 2026-08-21 session ("...an example of someone who...")
    produced 214-230 consecutive full stops in Malayalam, Telugu and Hindi and
    took 4.34s against a 1.10s mean. Because the GPU lock serialises the queue,
    the slowest segment also stalls every segment behind it.
    """
    t = _translator_with_fake_model()
    _run_with_timeout(lambda: t.translate_all_sync("an example of someone who"))

    kwargs = t.model.generate_kwargs
    assert kwargs["no_repeat_ngram_size"] >= 3, (
        "no n-gram repetition block: a degenerate decode runs to max_length"
    )
    assert kwargs["repetition_penalty"] > 1.0, "no repetition penalty"


# --- Malayalam output encoding ---------------------------------------------

def test_legacy_chillu_conjunct_is_normalized():
    """IndicTrans2 emits three different encodings of the same nṟa conjunct.

    The 2026-08-21 session contained legacy U+0D7B U+0D31 (11 times),
    U+0D7B U+0D4D U+0D31 (2) and standard U+0D28 U+0D4D U+0D31 (3). The first
    two render inconsistently across phones and Android WebView.
    """
    from translator import normalize_malayalam
    assert normalize_malayalam("തൻറെ") == \
        "തന്റെ"          # തൻറെ -> തന്റെ
    assert normalize_malayalam("ൻ്റെ") == \
        "ന്റെ"                # ൻ്റെ  -> ന്റെ


def test_deprecated_au_vowel_sign_is_normalized():
    """U+0D4C is deprecated in favour of U+0D57; 4 uses in one session."""
    from translator import normalize_malayalam
    assert normalize_malayalam("ഭൌമ") == "ഭൗമ"


def test_normalization_leaves_correct_malayalam_untouched():
    from translator import normalize_malayalam
    good = "തന്റെ"       # തന്റെ, already standard
    assert normalize_malayalam(good) == good
    assert normalize_malayalam("ജ്ഞാനം") == \
        "ജ്ഞാനം"    # ജ്ഞാനം


def test_malayalam_output_is_normalized_end_to_end():
    """The normaliser has to actually be wired into translate_all_sync.

    The fake decode deliberately carries all three artefacts through
    postprocess_batch, which leaves them untouched — so this test fails if
    normalize_malayalam() is written but never called.
    """
    dirty = "\u0d24\u0d7b\u0d31\u0d46 \u0d17\u0d41\u0d23\u0d02, \u0d2d\u0d4c\u0d2e\u0d3f\u0d15\u0d02"
    t = _translator_with_fake_model([dirty] + DECODED[1:])
    out = _run_with_timeout(lambda: t.translate_all_sync("his own quality, earthly"))

    assert "\u0d7b\u0d31" not in out["ml"], f"legacy chillu+rra survived: {out['ml']!r}"
    assert "\u0d7b\u0d4d\u0d31" not in out["ml"], f"chillu+virama+rra survived: {out['ml']!r}"
    assert "\u0d4c" not in out["ml"], f"deprecated au sign survived: {out['ml']!r}"
    # ...and the text is otherwise preserved, not blanked
    assert "\u0d28\u0d4d\u0d31" in out["ml"], f"conjunct lost entirely: {out['ml']!r}"


# --- NLLB fallback ----------------------------------------------------------

class _FakeNLLBTokenizer:
    """NLLB drives the tokenizer differently: src_lang attribute, one call per
    language, and a forced BOS token id looked up by name."""

    def __init__(self):
        self.src_lang = None
        self.calls = 0

    def __call__(self, text, **kwargs):
        self.calls += 1
        return _FakeEncoding(input_ids=[[1, 2]])

    def convert_tokens_to_ids(self, token):
        return 7

    def batch_decode(self, out, **kwargs):
        return list(out)


def _nllb_with_fake_model(decoded):
    import translator as T

    t = object.__new__(T.NLLBTranslator)
    t.device = "cpu"
    t.tokenizer = _FakeNLLBTokenizer()
    t.model = _FakeModel(decoded)
    return t


def test_nllb_fallback_also_guards_against_repetition_loops():
    """The fallback carries the same failure mode, so it needs the same guards."""
    t = _nllb_with_fake_model(["ഒന്ന്"] * 3)
    t.translate_all_sync("God is love.")

    kwargs = t.model.generate_kwargs
    assert kwargs["no_repeat_ngram_size"] >= 3
    assert kwargs["repetition_penalty"] > 1.0


def test_nllb_fallback_normalizes_malayalam_output():
    dirty = "തൻറെ"      # തൻറെ, legacy encoding
    t = _nllb_with_fake_model([dirty] * 3)

    out = t.translate_all_sync("his own")

    assert out["ml"] == "തന്റെ", f"got {out['ml']!r}"


# --- Glossary ---------------------------------------------------------------
# Terminology drift is the audit's cosmetic-but-confusing finding: "steward"
# appeared as four different Malayalam words in one sermon. Context makes drift
# less likely; only a glossary pins a term. Substitution is at STEM level so
# case suffixes survive -- Malayalam agentive nouns inflect on the stem.

def test_glossary_replaces_a_stem_and_keeps_the_suffix(monkeypatch):
    import translator as T
    # Illustrative only: real terms are a native-speaker decision, so
    # config.GLOSSARY ships empty.
    monkeypatch.setattr(T, "GLOSSARY", {"ml": {"കാവൽക്കാര": "കാര്യസ്ഥ"}})
    # nominative -ൻ, genitive -ന്റെ and plural -ർ all ride along
    assert T.apply_glossary("ml", "കാവൽക്കാരൻ") == "കാര്യസ്ഥൻ"
    assert T.apply_glossary("ml", "കാവൽക്കാരന്റെ") == "കാര്യസ്ഥന്റെ"
    assert T.apply_glossary("ml", "കാവൽക്കാരർ") == "കാര്യസ്ഥർ"


def test_glossary_leaves_other_languages_alone(monkeypatch):
    import translator as T
    monkeypatch.setattr(T, "GLOSSARY", {"ml": {"കാവൽക്കാര": "കാര്യസ്ഥ"}})
    assert T.apply_glossary("te", "కావൽക്കാര") == "కావൽക്കാര"


def test_glossary_prefers_the_longest_match(monkeypatch):
    """A short stem must not eat a longer one it is a prefix of."""
    import translator as T
    monkeypatch.setattr(T, "GLOSSARY", {"hi": {"मालिक": "स्वामी",
                                               "मालिकाना": "स्वामित्व"}})
    assert T.apply_glossary("hi", "मालिकाना हक") == "स्वामित्व हक"


def test_empty_glossary_is_a_noop(monkeypatch):
    """A language with no rows must be passed through untouched."""
    import translator as T
    monkeypatch.setattr(T, "GLOSSARY", {"ml": {}, "te": {}, "hi": {}})
    assert T.apply_glossary("ml", "കാവൽക്കാരൻ") == "കാവൽക്കാരൻ"
    assert T.apply_glossary("te", "స్టీవర్డ్") == "స్టీవర్డ్"


def test_shipped_glossary_pins_the_agreed_terms():
    """Terms chosen by the user after a replay showed what the model produces.

    steward: ml കാര്യസ്ഥൻ, te గృహనిర్వాహకుడు, hi കാരभारी
    master:  hi मालिक  (ml യജമാനൻ / te యజమాని already dominate unaided)
    """
    from translator import apply_glossary
    assert apply_glossary("ml", "കാവൽക്കാരൻ") == "കാര്യസ്ഥൻ"
    assert apply_glossary("te", "స్టీవర్డ్") == "గృహనిర్వాహకుడు"
    assert apply_glossary("hi", "प्रबंधक") == "कारभारी"
    assert apply_glossary("hi", "मास्टर") == "मालिक"


# Every inflected form the model actually produced in the 2026-08-21 replay,
# with the form it must become. The config comment demands each be checked;
# this is that check. A malformed result here is the exact bug that appeared
# when an earlier attempt mapped സ്റ്റീവർഡ് (which ends in a virama) instead of
# an inflecting stem.
OBSERVED_FORMS = [
    # (lang, produced, must_become)
    ("ml", "കാവൽക്കാരൻ",     "കാര്യസ്ഥൻ"),
    ("ml", "കാവൽക്കാരന്",    "കാര്യസ്ഥന്"),
    ("ml", "കാവൽക്കാരന്റെ",  "കാര്യസ്ഥന്റെ"),
    ("ml", "മേൽനോട്ടക്കാരൻ", "കാര്യസ്ഥൻ"),
    ("te", "స్టీవర్డ్",       "గృహనిర్వాహకుడు"),
    ("te", "స్టీవార్డులు",    "గృహనిర్వాహకులు"),
    ("te", "మాస్టర్",         "యజమాని"),
    ("hi", "प्रबंधक",         "कारभारी"),
    ("hi", "प्रबंधकों",       "कारभारियों"),
    ("hi", "मास्टर",          "मालिक"),
]


@pytest.mark.parametrize("lang,produced,expected", OBSERVED_FORMS)
def test_every_observed_form_maps_to_a_valid_form(lang, produced, expected):
    from translator import apply_glossary
    assert apply_glossary(lang, produced) == expected


def test_glossary_does_not_touch_the_already_correct_term():
    """The dominant, correct words must survive untouched."""
    from translator import apply_glossary
    assert apply_glossary("ml", "യജമാനൻ") == "യജമാനൻ"
    assert apply_glossary("te", "యజమాని") == "యజమాని"
    assert apply_glossary("hi", "कारभारी") == "कारभारी"
    assert apply_glossary("hi", "मालिक") == "मालिक"


# --- Sentence helpers for context recovery ---------------------------------

def test_last_sentence_of_the_context():
    from translator import last_sentence
    assert last_sentence("Not the master's wealth. Remember the system.") == \
        "Remember the system."
    assert last_sentence("Only one sentence.") == "Only one sentence."
    assert last_sentence("") == ""


def test_target_sentences_split_on_the_hindi_danda():
    """IndicTrans2 ends Hindi sentences with U+0964, not a full stop.

    Splitting only on "." would treat a whole Hindi paragraph as one sentence
    and the context-recovery step would return nothing.
    """
    from translator import split_target_sentences
    assert split_target_sentences("पहला वाक्य। दूसरा वाक्य।") == \
        ["पहला वाक्य।", "दूसरा वाक्य।"]
    assert split_target_sentences("ഒന്ന്. രണ്ട്.") == ["ഒന്ന്.", "രണ്ട്."]
    assert split_target_sentences("") == []


@pytest.fixture
def context_on(monkeypatch):
    """These tests exercise the context mechanism, which ships disabled."""
    import translator as T
    monkeypatch.setattr(T, "MT_USE_CONTEXT", True)


# --- Source-side context, with a content-integrity guard --------------------
# Measured on the four segments where "steward" drifts (2026-08-21 seg 3/8/9/13):
#   no context ......................... 4 distinct terms, 0 sentences lost
#   context = whole previous segment ... 2 distinct terms, 2 sentences LOST
#   context = last sentence only ....... 2 distinct terms, 0 sentences lost
# Target-side decoder priming also stabilised terminology but dropped the
# opening of 2 of 3 segments outright, so it is not used.

# Two target sentences per language: a context sentence and the real one.
TWO_SENT = [
    "पहला वाक्य । दूसरा वाक्य ।",     # -> Malayalam
    "पहला वाक्य । दूसरा वाक्य ।",     # -> Telugu
    "पहला वाक्य । दूसरा वाक्य ।",     # -> Hindi
]
ONE_SENT = ["केवल एक ।"] * 3


def test_context_is_prepended_to_the_source(context_on):
    t = _translator_with_fake_model([TWO_SENT])
    _run_with_timeout(lambda: t.translate_all_sync(
        "Stewards made their income.", context="Remember the economic system."))

    for sent in t.tokenizer.seen_batch:
        assert "Remember the economic system" in sent, sent
        assert "Stewards made their income" in sent, sent


def test_only_the_last_context_sentence_is_used(context_on):
    """Whole-segment context contaminated the current sentence; one is enough."""
    t = _translator_with_fake_model([TWO_SENT])
    _run_with_timeout(lambda: t.translate_all_sync(
        "Stewards made their income.",
        context="Not the master's wealth. Remember the economic system."))

    sent = t.tokenizer.seen_batch[0]
    assert "Remember the economic system" in sent
    assert "master" not in sent, f"carried more than one context sentence: {sent}"


def test_the_context_translation_is_discarded(context_on):
    t = _translator_with_fake_model([TWO_SENT])
    out = _run_with_timeout(lambda: t.translate_all_sync(
        "The real sentence.", context="The context sentence."))

    for lang in ("ml", "te", "hi"):
        assert len(split_target_sentences_for_test(out[lang])) == 1, out[lang]


def split_target_sentences_for_test(text):
    from translator import split_target_sentences
    return split_target_sentences(text)


def test_missing_content_falls_back_to_translating_without_context(context_on):
    """The guard that makes context safe.

    If the target comes back with fewer sentences than the source had, the
    context ate some of the real content — exactly what whole-segment context
    did to seg9. Retry that language with no context rather than ship a
    sentence the speaker never had translated.
    """
    # 1st call: only ONE target sentence, so nothing survives dropping the
    # context. 2nd call (the retry) returns proper content.
    t = _translator_with_fake_model([ONE_SENT, DECODED])
    out = _run_with_timeout(lambda: t.translate_all_sync(
        "The real sentence.", context="The context sentence."))

    assert t.model.generate_calls == 2, "did not retry without context"
    assert has_script(out["ml"], ML), out["ml"]
    assert out["ml"].strip(), "returned an empty translation"


def test_no_context_behaves_exactly_as_before():
    """Regression: the default path must not change."""
    t = _translator_with_fake_model()
    out = _run_with_timeout(lambda: t.translate_all_sync("God is love."))

    assert t.model.generate_calls == 1
    assert set(out) == {"ml", "te", "hi"}
    for sent in t.tokenizer.seen_batch:
        assert sent.count("God is love") == 1


async def test_translate_all_accepts_context_keyword(context_on):
    import asyncio
    t = _translator_with_fake_model([TWO_SENT])
    t._lock = asyncio.Lock()
    out = await t.translate_all("The real sentence.", context="The context one.")
    assert set(out) == {"ml", "te", "hi"}


def test_split_drops_punctuation_only_debris():
    """The context's translation can collapse to a bare full stop.

    Observed on 2026-08-21 seg8/seg20 with context enabled: the joined output
    began ". . " and that debris was shipped to the screens. Counting debris as
    a sentence also hides real content loss from the integrity guard.
    """
    from translator import split_target_sentences
    assert split_target_sentences(". . असली वाक्य।") == ["असली वाक्य।"]
    assert split_target_sentences(" . ") == []
    assert split_target_sentences("60 units.") == ["60 units."]   # digits count


def test_debris_context_triggers_the_integrity_guard(context_on):
    """If the context translated to nothing, dropping it would eat real content.

    After debris is filtered there is no context sentence left to drop, so the
    recovered text is short by one and the guard must retry without context
    rather than return a truncated caption.
    """
    debris = [". . पहला असली वाक्य । दूसरा असली वाक्य ।"] * 3
    t = _translator_with_fake_model([debris, DECODED])
    out = _run_with_timeout(lambda: t.translate_all_sync(
        "First real one. Second real one.", context="The context sentence."))

    assert t.model.generate_calls == 2, "shipped debris instead of retrying"
    assert not out["ml"].lstrip().startswith("."), out["ml"]


def test_context_ships_disabled():
    """Opt-in, because measured context introduced a negation that wasn't said.

    On 2026-08-21 seg20, context turned "respond accurately and appropriately"
    into Malayalam "...will NOT be responding accurately and appropriately"
    (ആയിരിക്കില്ല) and coined the malformed മേൽനോട്ടംക്കാരൻ. Against that it only
    took distinct "steward" terms from 3 to 2, at 1.4x the MT latency.
    The whole point of the audit was that fluent-but-wrong is the dangerous
    failure, so terminology is not worth an invented negation. Use GLOSSARY,
    which cannot change meaning. Turn this on only with A/B evidence for your
    own speaker.
    """
    from config import MT_USE_CONTEXT
    assert MT_USE_CONTEXT is False


# --- Source-side pre-editing -------------------------------------------------
# Some English words are mapped wrongly no matter how much context the model
# has. Rewriting the English before it reaches the translator fixes them; the
# screens and the transcript keep what the speaker actually said.

def test_contrast_is_rewritten_to_compare():
    """"to contrast X" came back as "is contrary to X" in all three languages.

    Measured: the original was wrong in ml, te AND hi; "compare" is correct in
    all three (ml താരതമ്യം, te పోల్చడం, hi तुलना). Also fixes seg28's
    "Now contrast that with the sons of light".
    """
    from translator import apply_source_rewrites
    assert apply_source_rewrites(
        "the purpose of the parable is to contrast moral character."
    ) == "the purpose of the parable is to compare moral character."


def test_rewrites_are_case_insensitive():
    """Target scripts have no case, so only the translator input is affected."""
    from translator import apply_source_rewrites
    assert "compare" in apply_source_rewrites("Contrast that with the sons of light.")


def test_not_dishonesty_is_restructured():
    """"praises shrewdness, not dishonesty" dropped the negative prefix.

    ml and te both returned "praises cleverness, not HONESTY". Measured:
    "rather than dishonest behaviour" is correct in all three, and both halves
    are needed — "not dishonest behaviour" alone made ml say Jesus PRAISES
    dishonest behaviour.
    """
    from translator import apply_source_rewrites
    assert apply_source_rewrites("Jesus praises shrewdness, not dishonesty.") == \
        "Jesus praises shrewdness rather than dishonest behaviour."


def test_rewrites_leave_ordinary_text_alone():
    from translator import apply_source_rewrites
    for s in ("God is love.", "He confronts reality.",
              "The steward reduced his own commission."):
        assert apply_source_rewrites(s) == s


# --- Per-sentence translation ------------------------------------------------

def test_multi_sentence_input_is_translated_one_sentence_at_a_time():
    """Cross-sentence bleed inside one input flipped who earned the commission.

    seg9 is "Not the master's wealth. Remember the economic system. Stewards
    made their income...". Translated whole, "master" from sentence 1 bled into
    sentence 3 and te/hi rendered "Stewards" as masters/owners. Each sentence
    alone is correct, so they go through as separate batch rows — still ONE
    generate call.
    """
    t = _translator_with_fake_model([DECODED * 3])   # 3 langs x 3 sentences
    _run_with_timeout(lambda: t.translate_all_sync(
        "First one. Second one. Third one."))

    assert t.model.generate_calls == 1, "must stay a single batched generate"
    assert len(t.tokenizer.seen_batch) == 9, (
        f"expected 3 languages x 3 sentences, got {len(t.tokenizer.seen_batch)}"
    )
    # each row carries exactly one sentence
    for row in t.tokenizer.seen_batch:
        assert sum(row.count(c) for c in ".?!") == 1, row


def test_single_sentence_input_is_unchanged():
    """Regression: the common case must still be 3 rows, one generate."""
    t = _translator_with_fake_model()
    out = _run_with_timeout(lambda: t.translate_all_sync("God is love."))
    assert t.model.generate_calls == 1
    assert len(t.tokenizer.seen_batch) == 3
    assert set(out) == {"ml", "te", "hi"}


def test_per_sentence_output_is_rejoined_in_order():
    t = _translator_with_fake_model([DECODED * 3])
    out = _run_with_timeout(lambda: t.translate_all_sync(
        "First one. Second one. Third one."))
    # three Malayalam sentences, joined
    from translator import split_target_sentences
    assert len(split_target_sentences(out["ml"])) == 3, out["ml"]


def test_source_sentence_split():
    from translator import split_source_sentences
    assert split_source_sentences("One. Two? Three!") == ["One.", "Two?", "Three!"]
    assert split_source_sentences("No terminator") == ["No terminator"]
    assert split_source_sentences("") == []
