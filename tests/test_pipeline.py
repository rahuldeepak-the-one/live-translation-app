from hub import BroadcastHub
from pipeline import UtterancePipeline
from tests.test_hub import FakeWS


class StubSTT:
    def __init__(self, text):
        self.text = text

    async def transcribe(self, audio_np):
        return self.text, "en"


class StubTranslator:
    async def translate_all(self, text, context=""):
        return {"ml": f"ml:{text}", "te": f"te:{text}", "hi": f"hi:{text}"}

    async def translate_sentences(self, sentences, context=""):
        # Mirrors the real interface: one dict per sentence, one call.
        return [{"ml": f"ml:{s}", "te": f"te:{s}", "hi": f"hi:{s}"}
                for s in sentences]


async def test_speech_flows_to_screens():
    clock = FakeClock()
    hub = BroadcastHub()
    screen = FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(StubSTT("Hello world."), StubTranslator(), hub,
                             clock=clock)

    result = await pipe.process(None)  # stub ignores audio
    await speaker_stops(pipe, clock)

    assert result == (1, "Hello world.")
    types = [m["type"] for m in screen.sent]
    # Stage 2b publishes the sentence twice: grey while held, solid once the
    # grace timer confirms no continuation is coming.
    assert types == ["history", "display", "sentence", "sentence", "translation"]
    translation = next(m for m in screen.sent if m["type"] == "translation")
    assert translation["ml"] == "ml:Hello world."


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
    assert [m["type"] for m in screen.sent] == ["history", "display"]


class SeqSTT:
    """Returns a scripted sequence of transcriptions, one per process() call."""
    def __init__(self, *texts):
        self.texts = list(texts)

    async def transcribe(self, audio_np):
        """Each entry is text, or (text, lang) for a non-English chunk."""
        item = self.texts.pop(0) if self.texts else ""
        return item if isinstance(item, tuple) else (item, "en")


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def kinds(screen):
    return [m["type"] for m in screen.sent]


async def speaker_stops(pipe, clock):
    """End the utterance the way stage 2b requires.

    A terminated tail is no longer flushed at its cut — it waits for the next
    chunk's first letter to confirm or deny Whisper's full stop. When no next
    chunk is coming, the grace timer is what delivers it.
    """
    from config import SENTENCE_GRACE_S
    clock.advance(SENTENCE_GRACE_S + 0.1)
    return await pipe.flush_if_stale()


async def test_complete_sentence_translates_once_the_speaker_stops():
    """Renamed by stage 2b: a full stop is a claim, not yet a decision.

    It used to translate at the cut. It now waits for the next chunk's first
    letter, because 26-41% of those full stops were Whisper inventing one at a
    breath. Nothing follows here, so the grace timer settles it.
    """
    clock = FakeClock()
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("God is love."), StubTranslator(), hub,
                             clock=clock)

    await pipe.process(None)
    assert kinds(screen) == ["history", "display", "sentence"]

    await speaker_stops(pipe, clock)

    assert kinds(screen) == ["history", "display", "sentence", "sentence",
                             "translation"]


async def test_unfinished_sentence_publishes_english_but_holds_translation():
    """A mid-sentence pause must not ship a fragment to the translator."""
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("But the translations"), StubTranslator(), hub)

    await pipe.process(None)

    assert kinds(screen) == ["history", "display", "sentence"]
    sentence = next(m for m in screen.sent if m["type"] == "sentence")
    assert sentence["en"] == "But the translations"


async def test_continuation_joins_and_translates_the_whole_sentence():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    clock = FakeClock()
    pipe = UtterancePipeline(
        SeqSTT("But the translations", "are good."), StubTranslator(), hub,
        clock=clock,
    )

    await pipe.process(None)
    result = await pipe.process(None)
    await speaker_stops(pipe, clock)

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
    assert kinds(screen) == ["history", "display", "sentence"]

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
    clock = FakeClock()
    pipe = UtterancePipeline(SeqSTT("Do you believe?", "Amen!"), StubTranslator(),
                             hub, clock=clock)

    await pipe.process(None)
    await pipe.process(None)
    await speaker_stops(pipe, clock)

    assert len([m for m in screen.sent if m["type"] == "translation"]) == 2


async def test_utterance_is_written_to_the_transcript():
    class FakeLog:
        def __init__(self):
            self.records = []

        def write(self, **record):
            self.records.append(record)

    log = FakeLog()
    clock = FakeClock()
    hub = BroadcastHub()
    pipe = UtterancePipeline(SeqSTT("God is love."), StubTranslator(), hub,
                             transcript=log, clock=clock)

    await pipe.process(None)
    await speaker_stops(pipe, clock)

    assert len(log.records) == 1
    assert log.records[0]["en"] == "God is love."
    assert log.records[0]["translations"]["te"] == "te:God is love."


async def test_colon_does_not_end_a_sentence():
    """"He said this:" introduces a quote — translating it alone is meaningless."""
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("And the Lord said this:"), StubTranslator(), hub)

    await pipe.process(None)

    assert kinds(screen) == ["history", "display", "sentence"]


async def test_semicolon_does_not_end_a_sentence():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(
        SeqSTT("The Lord is my shepherd;"), StubTranslator(), hub
    )

    await pipe.process(None)

    assert kinds(screen) == ["history", "display", "sentence"]


async def test_flush_if_stale_translates_an_abandoned_sentence():
    """The speaker's last words must not hang untranslated when audio stops."""
    from config import MAX_SENTENCE_HOLD_S
    clock = FakeClock()
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("and he never finished"), StubTranslator(), hub,
                            clock=clock)

    await pipe.process(None)
    assert kinds(screen) == ["history", "display", "sentence"]

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
    assert kinds(screen) == ["history", "display", "sentence"]


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

        async def translate_sentences(self, sentences, context=""):
            self.seen.extend(sentences)
            return [{"ml": "", "te": "", "hi": ""} for _ in sentences]

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

        async def translate_sentences(self, sentences, context=""):
            self.seen.extend(sentences)
            return [{"ml": "", "te": "", "hi": ""} for _ in sentences]

    rec = Rec()
    clock = FakeClock()
    pipe = UtterancePipeline(
        SeqSTT("The steward's unethical behavior is never...",
               "endorsed. His mismanagement is the reason he gets fired."),
        rec, BroadcastHub(), clock=clock,
    )

    await pipe.process(None)
    assert rec.seen == [], "flushed the trail-off instead of holding it"

    await pipe.process(None)
    await speaker_stops(pipe, clock)

    # Stage 2 publishes per sentence, so the joined text arrives as two rows.
    # The point of this test is unchanged: "is never" met "endorsed" instead of
    # being flushed alone, and they are in the SAME row.
    assert len(rec.seen) == 2, rec.seen
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

        async def translate_sentences(self, sentences, context=""):
            self.seen.extend(sentences)
            return [{"ml": "", "te": "", "hi": ""} for _ in sentences]

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
    await speaker_stops(pipe, clock)               # stage 2b holds the tail

    # Two complete sentences under Stage 2, one row each. What matters is that
    # the partial word was rejoined rather than flushed as a fragment.
    assert len(rec.seen) == 2, rec.seen
    assert "ability to understand his situation" in " ".join(rec.seen), rec.seen


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

    async def translate_sentences(self, sentences, context=""):
        # Stage 2 batches a chunk's sentences into one call; the context is the
        # sentence before the FIRST of them.
        for i, sentence in enumerate(sentences):
            self.calls.append((sentence, context if i == 0 else ""))
        return [{"ml": f"ml:{s}", "te": f"te:{s}", "hi": f"hi:{s}"}
                for s in sentences]


async def test_first_sentence_has_no_context():
    clock = FakeClock()
    tr = CtxTranslator()
    pipe = UtterancePipeline(SeqSTT("God is love."), tr, BroadcastHub(),
                             clock=clock)
    await pipe.process(None)
    await speaker_stops(pipe, clock)
    assert tr.calls == [("God is love.", "")]


async def test_the_previous_sentence_becomes_the_context():
    tr = CtxTranslator()
    clock = FakeClock()
    pipe = UtterancePipeline(
        SeqSTT("Remember the economic system.", "Stewards made their income."),
        tr, BroadcastHub(), clock=clock,
    )
    await pipe.process(None)
    await pipe.process(None)
    await speaker_stops(pipe, clock)

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
    await speaker_stops(pipe, clock)

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
    await speaker_stops(pipe, clock)

    assert tr.calls[1][1] == "", f"stale context carried: {tr.calls[1][1]!r}"


async def test_context_survives_a_normal_sentence_boundary():
    """Only silence clears it — consecutive sentences keep their context."""
    tr = CtxTranslator()
    clock = FakeClock()
    pipe = UtterancePipeline(
        SeqSTT("First one.", "Second one.", "Third one."), tr, BroadcastHub(),
        clock=clock,
    )
    for _ in range(3):
        await pipe.process(None)
    await speaker_stops(pipe, clock)

    assert tr.calls[1][1] == "First one."
    assert tr.calls[2][1] == "Second one."


# --- Stage 2b instrumentation -------------------------------------------------

async def test_chunk_text_callback_receives_the_raw_transcription():
    """The cut record needs THIS chunk's text, not the joined sentence.

    Ground truth for a bad boundary is whether the NEXT chunk starts lowercase,
    which only the raw per-chunk text can answer. process() returns the joined
    pending text, so the raw chunk is exposed through a callback rather than by
    changing the return shape and moving every existing caller.
    """
    seen = []
    hub = BroadcastHub()
    pipe = UtterancePipeline(StubSTT("with practical foresight."), StubTranslator(),
                             hub, on_chunk_text=seen.append)
    await pipe.process(None)
    assert seen == ["with practical foresight."]


async def test_chunk_text_callback_fires_per_chunk_not_per_sentence():
    # A held sentence spans several chunks; each must be reported separately,
    # because the join is exactly what we are trying to evaluate.
    seen = []
    hub = BroadcastHub()
    pipe = UtterancePipeline(SeqSTT("The purpose is to contrast moral character.",
                                    "with practical foresight."),
                             StubTranslator(), hub, on_chunk_text=seen.append)
    await pipe.process(None)
    await pipe.process(None)
    assert seen == ["The purpose is to contrast moral character.",
                    "with practical foresight."]


async def test_chunk_text_callback_is_optional():
    hub = BroadcastHub()
    pipe = UtterancePipeline(StubSTT("Hello."), StubTranslator(), hub)
    assert await pipe.process(None) == (1, "Hello.")


async def test_chunk_text_callback_not_called_on_an_empty_transcription():
    seen = []
    hub = BroadcastHub()
    pipe = UtterancePipeline(StubSTT(""), StubTranslator(), hub, on_chunk_text=seen.append)
    await pipe.process(None)
    assert seen == []


async def test_a_failing_callback_never_takes_the_service_down():
    # Instrumentation must not be able to drop an utterance. Same rule the
    # transcript log follows.
    def boom(_text):
        raise RuntimeError("instrumentation exploded")

    hub = BroadcastHub()
    pipe = UtterancePipeline(StubSTT("Hello."), StubTranslator(), hub, on_chunk_text=boom)
    assert await pipe.process(None) == (1, "Hello.")


async def test_no_clock_reading_inside_process_can_flush_a_fragment():
    """Only completeness and length decide the flush; elapsed time never does.

    process() used to compute `held_for` from two clock reads with nothing
    between them, so it was always ~0 and its `held_for >= MAX_SENTENCE_HOLD_S`
    disjunct could not fire. Worse than dead: had it measured anything it would
    have measured sentence AGE, the exact thing the 2026-08-21 audit removed in
    favour of silence (see test_hold_timeout_measures_silence_not_sentence_age).

    A clock that jumps a full hold between reads proves the disjunct is gone
    rather than merely quiet — under the old code this fragment reached the
    translator.
    """
    from config import MAX_SENTENCE_HOLD_S

    class JumpyClock:
        """Advances a full hold on every single read."""
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            self.t += MAX_SENTENCE_HOLD_S
            return self.t

    class Rec:
        def __init__(self):
            self.seen = []

        async def translate_all(self, text, context=""):
            self.seen.append(text)
            return {"ml": "", "te": "", "hi": ""}

        async def translate_sentences(self, sentences, context=""):
            self.seen.extend(sentences)
            return [{"ml": "", "te": "", "hi": ""} for _ in sentences]

    rec = Rec()
    pipe = UtterancePipeline(
        StubSTT("The steward's unethical behavior is never"),
        rec, BroadcastHub(), clock=JumpyClock(),
    )
    await pipe.process(None)

    assert rec.seen == [], f"a fragment reached the translator: {rec.seen}"
    assert pipe._pending is not None, "the fragment was not held"


# --- Stage 2: a sentence, not a chunk, is the published unit ------------------

class RecordingTranslator(StubTranslator):
    """Counts calls, so a per-sentence publish that lost batching is visible."""

    def __init__(self):
        self.sentence_calls = []

    async def translate_sentences(self, sentences, context=""):
        self.sentence_calls.append(list(sentences))
        return [{"ml": f"ml:{s}", "te": f"te:{s}", "hi": f"hi:{s}"} for s in sentences]


async def test_a_multi_sentence_chunk_publishes_each_sentence_separately():
    """One chunk was one id — median 3 sentences and 136 chars, so lanes moved
    in jumps and `final` stayed coarse. Each complete sentence is now its own."""
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(
        SeqSTT("Wisdom is not moral. It is practical. Amen."),
        RecordingTranslator(), hub)

    await pipe.process(None)

    sentences = [m for m in screen.sent if m["type"] == "sentence"]
    assert [m["en"] for m in sentences] == [
        "Wisdom is not moral.", "It is practical.", "Amen."]
    assert [m["id"] for m in sentences] == [1, 2, 3]
    # Stage 2b: the TAIL is held pending the next chunk's first letter, so it
    # is the one sentence not yet final. The earlier two are settled — their
    # boundaries came from Whisper's own punctuation mid-chunk, not from the
    # audio cut, so no breath can be hiding in them.
    assert [m["final"] for m in sentences] == [True, True, False]


async def test_every_sentence_gets_its_own_translation():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    clock = FakeClock()
    pipe = UtterancePipeline(SeqSTT("God is love. He loves the world."),
                             RecordingTranslator(), hub, clock=clock)

    await pipe.process(None)
    await speaker_stops(pipe, clock)

    translations = [m for m in screen.sent if m["type"] == "translation"]
    assert [m["id"] for m in translations] == [1, 2]
    assert translations[0]["ml"] == "ml:God is love."
    assert translations[1]["ml"] == "ml:He loves the world."


async def test_a_chunk_costs_ONE_translator_call_however_many_sentences():
    """Publishing per sentence must not become one GPU round trip per sentence.

    The chunked engine translated a 3-sentence chunk in a single batched call
    (mt median 0.8s on 2026-08-21). Three serialised calls behind the GPU lock
    would undo that for no benefit.
    """
    hub = BroadcastHub()
    translator = RecordingTranslator()
    pipe = UtterancePipeline(SeqSTT("One. Two. Three."), translator, hub)

    await pipe.process(None)

    assert len(translator.sentence_calls) == 1, translator.sentence_calls
    # Stage 2b holds the tail for the next chunk's first letter, so the batch is
    # every sentence this chunk COMPLETED. Still one call, not one per
    # sentence — which is the property this test exists to protect.
    assert translator.sentence_calls[0] == ["One.", "Two."]


async def test_a_trailing_incomplete_sentence_stays_pending():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("Wisdom is not moral. But it is"),
                             RecordingTranslator(), hub)

    await pipe.process(None)

    sentences = [m for m in screen.sent if m["type"] == "sentence"]
    assert [(m["en"], m["final"]) for m in sentences] == [
        ("Wisdom is not moral.", True),
        ("But it is", False),
    ]
    # only the complete one reached the translator
    translations = [m for m in screen.sent if m["type"] == "translation"]
    assert [m["id"] for m in translations] == [1]


async def test_the_pending_sentence_keeps_its_id_across_chunks():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    clock = FakeClock()
    pipe = UtterancePipeline(SeqSTT("First done. But it is", "practical."),
                             RecordingTranslator(), hub, clock=clock)

    await pipe.process(None)
    await pipe.process(None)
    await speaker_stops(pipe, clock)

    sentences = [m for m in screen.sent if m["type"] == "sentence"]
    # id 2 is revised in place, never duplicated under a new id. Stage 2b adds
    # the last row: the completed sentence is still grey until the grace timer
    # confirms nothing follows it, then republished solid under the same id.
    assert [(m["id"], m["en"], m["final"]) for m in sentences] == [
        (1, "First done.", True),
        (2, "But it is", False),
        (2, "But it is practical.", False),
        (2, "But it is practical.", True),
    ]


async def test_ids_stay_monotonic_across_chunks():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("A one. A two.", "A three."),
                             RecordingTranslator(), hub)

    await pipe.process(None)
    await pipe.process(None)

    ids = [m["id"] for m in screen.sent if m["type"] == "sentence"]
    assert ids == sorted(ids)
    # id 2 appears twice: held grey at its cut, then republished solid when the
    # uppercase "A three." confirmed its full stop was real.
    assert ids == [1, 2, 2, 3]


async def test_a_single_complete_sentence_still_works():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    clock = FakeClock()
    pipe = UtterancePipeline(SeqSTT("God is love."), RecordingTranslator(), hub,
                             clock=clock)

    assert await pipe.process(None) == (1, "God is love.")
    await speaker_stops(pipe, clock)
    assert [m["type"] for m in screen.sent] == [
        "history", "display", "sentence", "sentence", "translation"]


async def test_each_sentence_is_written_to_the_transcript_separately():
    class FakeLog:
        def __init__(self):
            self.records = []

        def write(self, **record):
            self.records.append(record)

    log = FakeLog()
    clock = FakeClock()
    hub = BroadcastHub()
    pipe = UtterancePipeline(SeqSTT("God is love. He loves the world."),
                             RecordingTranslator(), hub, transcript=log,
                             clock=clock)

    await pipe.process(None)
    await speaker_stops(pipe, clock)

    assert [r["en"] for r in log.records] == ["God is love.", "He loves the world."]
    assert [r["id"] for r in log.records] == [1, 2]


# --- Stage 2b: a breath is not a full stop -----------------------------------
# Measured on 2026-08-30 (n=166 cuts flushed as endings) and 2026-08-23 (n=410):
# 26% and 41% of them were wrong — the next chunk continued the sentence. The
# gap-to-next-speech populations OVERLAP completely (116 of 119 real endings on
# 08-30 are shorter than the longest breath), so no timer can tell the two
# apart. What does tell them apart is the next chunk's first letter: Whisper
# capitalises a new sentence, and every continuation chunk observed began
# lowercase. So the flush decision waits for that letter.
#
# The false terminator matters as much as the hold. In 43/43 and 165/165 breath
# cases the held text already ends in ".", which split_source_sentences() would
# re-split at — a join alone is a no-op, the "." has to go.

class SeenTranslator:
    """Records every source string that reaches the translator."""
    def __init__(self):
        self.seen = []

    async def translate_all(self, text, context=""):
        self.seen.append(text)
        return {"ml": f"ml:{text}", "te": f"te:{text}", "hi": f"hi:{text}"}

    async def translate_sentences(self, sentences, context=""):
        self.seen.extend(sentences)
        return [{"ml": f"ml:{s}", "te": f"te:{s}", "hi": f"hi:{s}"}
                for s in sentences]


async def test_a_terminated_tail_is_held_instead_of_translated():
    """Whisper's full stop at a cut is a claim, not evidence. Wait for proof."""
    rec = SeenTranslator()
    pipe = UtterancePipeline(SeqSTT("someone who acts."), rec, BroadcastHub(),
                             clock=FakeClock())

    await pipe.process(None)

    assert rec.seen == [], "translated the tail before the next chunk could deny it"


async def test_a_held_tail_publishes_english_as_not_final():
    """It may still change, and `final` is the entire correction contract."""
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("someone who acts."), SeenTranslator(),
                             hub, clock=FakeClock())

    await pipe.process(None)

    sentence = next(m for m in screen.sent if m["type"] == "sentence")
    assert sentence["final"] is False


async def test_a_lowercase_next_chunk_strips_the_false_terminator_and_joins():
    """The 2026-08-30 seam: '...in spite of his big understanding.' + 'under the'.

    A join that leaves the "." in place is a no-op — split_source_sentences()
    re-splits at it and the translator sees the same two fragments.
    """
    from config import SENTENCE_GRACE_S
    clock = FakeClock()
    rec = SeenTranslator()
    pipe = UtterancePipeline(
        SeqSTT("someone who acts.", "with practical foresight."),
        rec, BroadcastHub(), clock=clock,
    )

    await pipe.process(None)
    await pipe.process(None)
    # The JOINED text ends in a full stop too, so it is itself a held tail
    # awaiting its own deciding letter. The speaker stops instead.
    clock.advance(SENTENCE_GRACE_S + 0.1)
    await pipe.flush_if_stale()

    assert rec.seen == ["someone who acts with practical foresight."], rec.seen


async def test_a_lowercase_continuation_keeps_one_sentence_id():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(
        SeqSTT("someone who acts.", "with practical foresight."),
        SeenTranslator(), hub, clock=FakeClock(),
    )

    await pipe.process(None)
    await pipe.process(None)

    ids = {m["id"] for m in screen.sent if m["type"] == "sentence"}
    assert ids == {1}


async def test_an_uppercase_next_chunk_flushes_the_held_sentence_alone():
    """A real ending must not be welded into a run-on the splitter cannot undo.

    This is the risk the capitalisation gate exists to avoid: ~97% of real
    endings resume speech inside any workable grace window, so a pure timer
    would join nearly all of them.
    """
    rec = SeenTranslator()
    pipe = UtterancePipeline(
        SeqSTT("The steward is not an example of righteousness.",
               "He is an example of urgency."),
        rec, BroadcastHub(), clock=FakeClock(),
    )

    await pipe.process(None)
    await pipe.process(None)

    assert rec.seen == ["The steward is not an example of righteousness."], rec.seen


async def test_an_uppercase_next_chunk_gets_a_fresh_id():
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(
        SeqSTT("God is love.", "He gave his only son."),
        SeenTranslator(), hub, clock=FakeClock(),
    )

    await pipe.process(None)
    await pipe.process(None)

    ids = [m["id"] for m in screen.sent if m["type"] == "sentence"]
    assert ids == [1, 1, 2], ids


async def test_the_grace_timer_flushes_a_held_tail_when_the_speaker_stops():
    """No next chunk is ever coming — the sentence must still be translated."""
    from config import SENTENCE_GRACE_S
    clock = FakeClock()
    rec = SeenTranslator()
    pipe = UtterancePipeline(SeqSTT("God is love."), rec, BroadcastHub(),
                             clock=clock)

    await pipe.process(None)
    assert rec.seen == []

    clock.advance(SENTENCE_GRACE_S + 0.1)
    assert await pipe.flush_if_stale() == 1
    assert rec.seen == ["God is love."]


async def test_the_grace_timer_flushes_the_tail_with_its_terminator_intact():
    """Nothing denied the full stop, so it stands."""
    from config import SENTENCE_GRACE_S
    clock = FakeClock()
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(SeqSTT("God is love."), SeenTranslator(), hub,
                             clock=clock)

    await pipe.process(None)
    clock.advance(SENTENCE_GRACE_S + 0.1)
    await pipe.flush_if_stale()

    finals = [m for m in screen.sent if m["type"] == "sentence" and m["final"]]
    assert finals and finals[-1]["en"] == "God is love."


async def test_speech_still_audible_holds_the_grace_timer_open():
    """The trap this design would otherwise walk into.

    Speech resumes ~1.3s after the cut, but the chunk carrying it is not cut
    and transcribed for a median 6.2s. A grace timer counting elapsed time
    would fire in between and flush before the deciding letter ever existed.
    So the timer measures SILENCE: audible speech refreshes it.
    """
    from config import SENTENCE_GRACE_S
    clock = FakeClock()
    rec = SeenTranslator()
    pipe = UtterancePipeline(SeqSTT("someone who acts."), rec, BroadcastHub(),
                             clock=clock)

    await pipe.process(None)
    clock.advance(SENTENCE_GRACE_S + 0.1)

    assert await pipe.flush_if_stale(has_speech=True) is None
    assert rec.seen == [], "flushed while the speaker was still audible"


async def test_an_unterminated_hold_still_waits_the_full_measured_constant():
    """MAX_SENTENCE_HOLD_S was measured against real gaps. 2b must not move it.

    A fragment's continuation cannot arrive before the next chunk is cut, and
    the grace timer is far shorter than that. Applying the grace to an
    unterminated fragment would re-create the 4.0s bug the 16s constant fixed.
    """
    from config import SENTENCE_GRACE_S
    clock = FakeClock()
    rec = SeenTranslator()
    pipe = UtterancePipeline(SeqSTT("The man's ability to un"), rec,
                             BroadcastHub(), clock=clock)

    await pipe.process(None)
    clock.advance(SENTENCE_GRACE_S + 0.1)

    assert await pipe.flush_if_stale() is None
    assert rec.seen == []


def test_the_grace_is_shorter_than_the_unterminated_hold():
    """They answer different questions and must not be the same number.

    The grace bounds a wait for a letter that arrives in ~6s; the hold bounds a
    wait for a continuation that cannot arrive before a chunk is cut.
    """
    from config import SENTENCE_GRACE_S, MAX_SENTENCE_HOLD_S
    assert 0 < SENTENCE_GRACE_S < MAX_SENTENCE_HOLD_S


def test_the_grace_covers_the_measured_real_ending_gaps():
    """p90 of real-ending speech gaps: 1.57s (2026-08-30), 2.22s (2026-08-23).

    Below that and the timer fires on sentences whose continuation was about to
    arrive, which is the failure the whole design is avoiding.
    """
    from config import SENTENCE_GRACE_S
    assert SENTENCE_GRACE_S >= 2.22


async def test_english_is_published_before_the_previous_sentence_is_translated():
    """English first, always — confirming an ending must not stall the live lane.

    When this chunk's capital letter confirms the held sentence really ended,
    that sentence still needs translating: a GPU round trip of ~0.8s behind a
    lock. Doing it before publishing THIS chunk's English would put that delay
    straight onto the caption lane the whole design keeps live.
    """
    hub, screen = BroadcastHub(), FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(
        SeqSTT("God is love.", "He gave his only son."),
        SeenTranslator(), hub, clock=FakeClock(),
    )

    await pipe.process(None)
    await pipe.process(None)

    seq = [(m["type"], m.get("id")) for m in screen.sent]
    assert seq.index(("sentence", 2)) < seq.index(("translation", 1)), seq


# --- 5-language upgrade: a chunk not in English ------------------------------

async def test_a_non_english_chunk_is_shown_as_said_and_never_welded():
    """The translator is en->indic only, so a Malayalam chunk cannot be
    translated; it must still reach its own lane, and must not be joined onto
    the English sentence held before it."""
    hub = BroadcastHub()
    screen = FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(
        SeqSTT("and the steward said", ("ദൈവം സ്നേഹമാണ്", "ml")),
        StubTranslator(), hub)

    await pipe.process(None)                       # held: unfinished English
    sid, text = await pipe.process(None)

    assert text == "ദൈവം സ്നേഹമാണ്"
    translations = [m for m in screen.sent if m["type"] == "translation"]
    # the held English was flushed and translated first, under its own id
    assert translations[0]["ml"] == "ml:and the steward said"
    assert translations[-1]["id"] == sid
    # as said, in every lane: "" would read as a failed translation on the wall
    assert translations[-1]["ml"] == translations[-1]["te"] == "ദൈവം സ്നേഹമാണ്"


def test_language_choice_defaults_to_english_for_an_accent():
    from functools import partial
    import stt
    # The five, not config's current set: this tests the rule, and config may
    # run English-only (it does since the 2026-10-10 turbo rollback).
    choose_language = partial(stt.choose_language, allowed=("en", "ml", "hi", "pa", "te"))
    # Indian-accented English read as Hindi, but not convincingly
    assert choose_language([("hi", 0.42), ("en", 0.40), ("ur", 0.1)]) == "en"
    # a language outside the five never takes the chunk, however sure
    assert choose_language([("ur", 0.9), ("en", 0.05)]) == "en"
    assert choose_language([("ml", 0.81), ("en", 0.1)]) == "ml"
    assert choose_language([("en", 0.97)]) == "en"
