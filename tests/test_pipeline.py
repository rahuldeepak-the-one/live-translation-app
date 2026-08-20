from hub import BroadcastHub
from pipeline import UtterancePipeline
from tests.test_hub import FakeWS


class StubSTT:
    def __init__(self, text):
        self.text = text

    async def transcribe(self, audio_np):
        return self.text


class StubTranslator:
    async def translate_all(self, text, context=""):
        return {"ml": f"ml:{text}", "te": f"te:{text}", "hi": f"hi:{text}"}


async def test_speech_flows_to_screens():
    hub = BroadcastHub()
    screen = FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(StubSTT("Hello world."), StubTranslator(), hub)

    result = await pipe.process(None)  # stub ignores audio

    assert result == (1, "Hello world.")
    types = [m["type"] for m in screen.sent]
    assert types == ["history", "sentence", "translation"]
    assert screen.sent[2]["ml"] == "ml:Hello world."


async def test_ids_increment():
    hub = BroadcastHub()
    pipe = UtterancePipeline(StubSTT("Hi."), StubTranslator(), hub)
    assert (await pipe.process(None))[0] == 1
    assert (await pipe.process(None))[0] == 2


async def test_empty_transcription_publishes_nothing():
    hub = BroadcastHub()
    screen = FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(StubSTT(""), StubTranslator(), hub)

    assert await pipe.process(None) is None
    assert [m["type"] for m in screen.sent] == ["history"]


class SeqSTT:
    """Returns a scripted sequence of transcriptions, one per process() call."""
    def __init__(self, *texts):
        self.texts = list(texts)

    async def transcribe(self, audio_np):
        return self.texts.pop(0) if self.texts else ""


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def kinds(screen):
    return [m["type"] for m in screen.sent]


async def test_complete_sentence_translates_immediately():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("God is love."), StubTranslator(), hub)

    await pipe.process(None)

    assert kinds(screen) == ["history", "sentence", "translation"]


async def test_unfinished_sentence_publishes_english_but_holds_translation():
    """A mid-sentence pause must not ship a fragment to the translator."""
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("But the translations"), StubTranslator(), hub)

    await pipe.process(None)

    assert kinds(screen) == ["history", "sentence"]
    assert screen.sent[1]["en"] == "But the translations"


async def test_continuation_joins_and_translates_the_whole_sentence():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(
        SeqSTT("But the translations", "are good."), StubTranslator(), hub
    )

    await pipe.process(None)
    result = await pipe.process(None)

    assert result == (1, "But the translations are good.")
    translation = [m for m in screen.sent if m["type"] == "translation"]
    assert len(translation) == 1
    assert translation[0]["id"] == 1
    assert translation[0]["ml"] == "ml:But the translations are good."


async def test_continuation_reuses_the_same_sentence_id():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("First half", "second half."), StubTranslator(), hub)

    await pipe.process(None)
    await pipe.process(None)

    ids = {m["id"] for m in screen.sent if m["type"] == "sentence"}
    assert ids == {1}


async def test_speech_after_a_long_silence_starts_a_new_sentence():
    """A held fragment is flushed on its own, not welded to unrelated speech.

    Behaviour change (2026-08-21 audit): the hold clock now measures silence, so
    speech arriving after it expires no longer joins the held fragment. Sixteen
    seconds of silence means the speaker moved on, and joining across it
    produced sentences the speaker never said. In production the mic socket's
    flush_if_stale() has usually already fired by this point; this covers the
    case where process() is the first call after the gap.
    """
    from config import MAX_SENTENCE_HOLD_S
    clock = FakeClock()
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(
        SeqSTT("He trails off", "and never finishes"), StubTranslator(), hub,
        clock=clock,
    )

    await pipe.process(None)
    assert kinds(screen) == ["history", "sentence"]

    clock.advance(MAX_SENTENCE_HOLD_S + 0.1)
    await pipe.process(None)

    translation = [m for m in screen.sent if m["type"] == "translation"]
    assert len(translation) == 1
    assert translation[0]["ml"] == "ml:He trails off"


async def test_next_sentence_gets_a_fresh_id_after_a_flush():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("One.", "Two."), StubTranslator(), hub)

    assert (await pipe.process(None))[0] == 1
    assert (await pipe.process(None))[0] == 2


async def test_question_and_exclamation_end_a_sentence():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("Do you believe?", "Amen!"), StubTranslator(), hub)

    await pipe.process(None)
    await pipe.process(None)

    assert len([m for m in screen.sent if m["type"] == "translation"]) == 2


async def test_utterance_is_written_to_the_transcript():
    class FakeLog:
        def __init__(self):
            self.records = []

        def write(self, **record):
            self.records.append(record)

    log = FakeLog()
    hub = BroadcastHub()
    pipe = UtterancePipeline(SeqSTT("God is love."), StubTranslator(), hub,
                             transcript=log)

    await pipe.process(None)

    assert len(log.records) == 1
    assert log.records[0]["en"] == "God is love."
    assert log.records[0]["translations"]["te"] == "te:God is love."


async def test_colon_does_not_end_a_sentence():
    """"He said this:" introduces a quote — translating it alone is meaningless."""
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("And the Lord said this:"), StubTranslator(), hub)

    await pipe.process(None)

    assert kinds(screen) == ["history", "sentence"]


async def test_semicolon_does_not_end_a_sentence():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(
        SeqSTT("The Lord is my shepherd;"), StubTranslator(), hub
    )

    await pipe.process(None)

    assert kinds(screen) == ["history", "sentence"]


async def test_flush_if_stale_translates_an_abandoned_sentence():
    """The speaker's last words must not hang untranslated when audio stops."""
    from config import MAX_SENTENCE_HOLD_S
    clock = FakeClock()
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("and he never finished"), StubTranslator(), hub,
                            clock=clock)

    await pipe.process(None)
    assert kinds(screen) == ["history", "sentence"]

    clock.advance(MAX_SENTENCE_HOLD_S + 0.1)
    assert await pipe.flush_if_stale() == 1

    translation = [m for m in screen.sent if m["type"] == "translation"]
    assert len(translation) == 1
    assert translation[0]["ml"] == "ml:and he never finished"


async def test_flush_if_stale_is_a_noop_before_the_hold_expires():
    clock = FakeClock()
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("still going"), StubTranslator(), hub, clock=clock)

    await pipe.process(None)
    clock.advance(0.2)

    assert await pipe.flush_if_stale() is None
    assert kinds(screen) == ["history", "sentence"]


async def test_flush_if_stale_is_a_noop_with_nothing_pending():
    hub = BroadcastHub()
    pipe = UtterancePipeline(SeqSTT("Complete."), StubTranslator(), hub)
    await pipe.process(None)
    assert await pipe.flush_if_stale() is None


# --- Fragment translation: the 2026-08-21 session shipped 11 of 29 segments
# --- as fragments, three of which reversed the speaker's meaning.

def test_sentence_hold_spans_at_least_one_audio_chunk():
    """A fragment's continuation cannot arrive before the next chunk is cut.

    Audio is buffered up to MAX_BUFFER_S before Whisper ever sees it, so a hold
    shorter than that expires while the rest of the sentence is still being
    recorded. On 2026-08-21 the hold was 4.0s against a median 7.9s gap: it
    expired before the continuation existed in 26 of 28 cases, and "...is
    never" / "endorsed." were translated as separate sentences — inverting the
    sermon in all three languages.
    """
    from config import MAX_SENTENCE_HOLD_S, MAX_BUFFER_S
    # One buffer period is not enough. AudioBuffer.should_process() refuses a
    # silence-only buffer WITHOUT clearing it, so a quiet stretch defers the cut
    # while the buffer keeps filling — observed gaps reached 14s against an 8s
    # MAX_BUFFER_S. Two buffer periods covered all 28 gaps in that session.
    assert MAX_SENTENCE_HOLD_S >= 2 * MAX_BUFFER_S, (
        f"hold {MAX_SENTENCE_HOLD_S}s < 2 x chunk {MAX_BUFFER_S}s: fragments "
        "will be force-translated before their continuation is recorded"
    )


def test_trailing_ellipsis_is_stripped_before_translation():
    """A dangling "..." carries no meaning and seeds decoder repetition loops.

    Segment 3 of the 2026-08-21 session ended "an example of someone who..."
    and all three languages ran to the 256-token cap emitting one repeated
    character (~220 of them), taking 4.34s against a 1.10s mean.
    """
    from pipeline import clean_for_translation
    assert clean_for_translation("an example of someone who...") == \
        "an example of someone who"


def test_trailing_comma_is_stripped_before_translation():
    from pipeline import clean_for_translation
    assert clean_for_translation("Out of all the parables Jesus told,") == \
        "Out of all the parables Jesus told"


def test_terminal_punctuation_survives_cleaning():
    """A complete sentence must reach the translator intact."""
    from pipeline import clean_for_translation
    assert clean_for_translation("God is love.") == "God is love."
    assert clean_for_translation("Do you believe?") == "Do you believe?"


async def test_fragment_reaches_the_translator_without_its_ellipsis():
    class RecordingTranslator:
        def __init__(self):
            self.seen = []

        async def translate_all(self, text, context=""):
            self.seen.append(text)
            return {"ml": "", "te": "", "hi": ""}

    from config import MAX_SENTENCE_HOLD_S
    clock = FakeClock()
    translator = RecordingTranslator()
    pipe = UtterancePipeline(
        SeqSTT("an example of someone who..."), translator, BroadcastHub(),
        clock=clock,
    )

    await pipe.process(None)
    clock.advance(MAX_SENTENCE_HOLD_S + 0.1)
    await pipe.flush_if_stale()

    assert translator.seen == ["an example of someone who"]


def test_pending_char_cap_allows_two_chunks_to_join():
    """The char cap is a backstop, not the normal path.

    Raising MAX_SENTENCE_HOLD_S only helps if a fragment can actually be joined
    to the chunk that completes it. Observed chunks on 2026-08-21 ran 116-175
    characters, so a cap below ~350 would flush the join as a fragment and undo
    the hold fix. Note looks_complete() is checked first, so a properly
    punctuated sentence longer than the cap still flushes normally.
    """
    from config import MAX_PENDING_CHARS
    assert MAX_PENDING_CHARS >= 350


def test_trailing_ellipsis_does_not_look_complete():
    """A trail-off is the opposite of a closed sentence.

    Whisper marks a cut-off utterance with an ellipsis, and its final "." made
    looks_complete() fire — flushing the fragment instantly, before the hold
    could ever join it. That is how "The steward's unethical behavior is
    never..." reached the translator without its verb and came back as "it was
    endorsed" in all three languages on 2026-08-21.
    """
    from pipeline import looks_complete
    assert looks_complete("The steward's unethical behavior is never...") is False
    assert looks_complete("So let's dive into what Jesus is…") is False


def test_real_sentence_endings_still_look_complete():
    from pipeline import looks_complete
    assert looks_complete("God is love.") is True
    assert looks_complete("Do you believe?") is True
    assert looks_complete("Amen!") is True


async def test_ellipsis_fragment_is_held_and_joined_not_flushed():
    """The seg17/18 reversal, end to end: 'is never' must meet 'endorsed'."""
    class Rec:
        def __init__(self):
            self.seen = []

        async def translate_all(self, text, context=""):
            self.seen.append(text)
            return {"ml": "", "te": "", "hi": ""}

    rec = Rec()
    pipe = UtterancePipeline(
        SeqSTT("The steward's unethical behavior is never...",
               "endorsed. His mismanagement is the reason he gets fired."),
        rec, BroadcastHub(), clock=FakeClock(),
    )

    await pipe.process(None)
    assert rec.seen == [], "flushed the trail-off instead of holding it"

    await pipe.process(None)

    assert len(rec.seen) == 1
    assert "is never endorsed" in rec.seen[0], rec.seen[0]


async def test_hold_timeout_measures_silence_not_sentence_age():
    """The timeout is for a speaker who stopped, not one who is still talking.

    The hold clock started when the sentence began, so a sentence spanning
    three chunks aged out even though speech kept arriving. That is how
    segments 17-19 of 2026-08-21 flushed "...The man's ability to un" as a
    fragment while its continuation was only 4s away: seg17 had already been
    pending for 12s, so the joined text was 16s "old" the moment seg18 landed.
    MAX_PENDING_CHARS, not the clock, is what bounds a long sentence.
    """
    from config import MAX_SENTENCE_HOLD_S

    class Rec:
        def __init__(self):
            self.seen = []

        async def translate_all(self, text, context=""):
            self.seen.append(text)
            return {"ml": "", "te": "", "hi": ""}

    clock, rec = FakeClock(), Rec()
    pipe = UtterancePipeline(
        SeqSTT("The steward's unethical behavior is never...",
               "endorsed. But Jesus draws attention to the man's ability to un-",
               "understand his situation clearly."),
        rec, BroadcastHub(), clock=clock,
    )

    await pipe.process(None)                       # fragment, pending
    clock.advance(MAX_SENTENCE_HOLD_S - 4)         # long gap, still under
    assert await pipe.flush_if_stale() is None

    await pipe.process(None)                       # speech arrives, still a fragment
    clock.advance(4)                               # only 4s of silence since then
    assert await pipe.flush_if_stale() is None, (
        "flushed a fragment while the speaker was still going"
    )

    await pipe.process(None)                       # the continuation lands

    assert len(rec.seen) == 1
    assert "ability to understand his situation" in rec.seen[0], rec.seen[0]


def test_hyphen_cut_partial_word_is_dropped_at_the_join():
    """Whisper cuts a word at a chunk edge and re-transcribes it whole.

    Segment 18 of 2026-08-21 ended "The man's ability to un-" and segment 19
    began "understand his situation". Concatenating gives "un- understand";
    dropping the partial gives the sentence the speaker actually said.
    """
    from pipeline import drop_partial_word
    assert drop_partial_word("The man's ability to un-") == "The man's ability to"
    assert drop_partial_word("a self-serving man") == "a self-serving man"
    assert drop_partial_word("God is love.") == "God is love."


# --- Translation context ----------------------------------------------------

class CtxTranslator:
    """Records the (text, context) pair handed to the translator."""

    def __init__(self):
        self.calls = []

    async def translate_all(self, text, context=""):
        self.calls.append((text, context))
        return {"ml": f"ml:{text}", "te": f"te:{text}", "hi": f"hi:{text}"}


async def test_first_sentence_has_no_context():
    tr = CtxTranslator()
    pipe = UtterancePipeline(SeqSTT("God is love."), tr, BroadcastHub())
    await pipe.process(None)
    assert tr.calls == [("God is love.", "")]


async def test_the_previous_sentence_becomes_the_context():
    tr = CtxTranslator()
    pipe = UtterancePipeline(
        SeqSTT("Remember the economic system.", "Stewards made their income."),
        tr, BroadcastHub(),
    )
    await pipe.process(None)
    await pipe.process(None)

    assert tr.calls[1] == ("Stewards made their income.",
                           "Remember the economic system.")


async def test_context_is_the_cleaned_text_not_the_raw_english():
    """A trailing ellipsis must not be carried into the next sentence either."""
    from config import MAX_SENTENCE_HOLD_S
    clock = FakeClock()
    tr = CtxTranslator()
    pipe = UtterancePipeline(
        SeqSTT("He trails off...", "A brand new sentence."), tr, BroadcastHub(),
        clock=clock,
    )
    await pipe.process(None)
    clock.advance(MAX_SENTENCE_HOLD_S + 0.1)
    await pipe.flush_if_stale()
    await pipe.process(None)

    assert tr.calls[0][0] == "He trails off"
    assert "..." not in tr.calls[1][1], tr.calls[1][1]


async def test_a_long_silence_clears_the_context():
    """After the speaker stops, the next sentence is a new thought.

    Carrying terminology across a pause means a choice made before the pastor
    changed passage keeps propagating for the rest of the service.
    """
    from config import MAX_SENTENCE_HOLD_S
    clock = FakeClock()
    tr = CtxTranslator()
    pipe = UtterancePipeline(
        SeqSTT("He never finished this one", "A completely new thought."),
        tr, BroadcastHub(), clock=clock,
    )

    await pipe.process(None)
    clock.advance(MAX_SENTENCE_HOLD_S + 0.1)
    await pipe.flush_if_stale()          # speaker stopped -> context is stale
    await pipe.process(None)

    assert tr.calls[1][1] == "", f"stale context carried: {tr.calls[1][1]!r}"


async def test_context_survives_a_normal_sentence_boundary():
    """Only silence clears it — consecutive sentences keep their context."""
    tr = CtxTranslator()
    pipe = UtterancePipeline(
        SeqSTT("First one.", "Second one.", "Third one."), tr, BroadcastHub(),
    )
    for _ in range(3):
        await pipe.process(None)

    assert tr.calls[1][1] == "First one."
    assert tr.calls[2][1] == "Second one."
