"""Hallucination filtering — pure logic, no model load (so not marked slow)."""
from stt import keep_segment, strip_hallucinations


def seg(text, no_speech_prob=0.1, avg_logprob=-0.3, compression_ratio=1.4):
    return dict(text=text, no_speech_prob=no_speech_prob,
                avg_logprob=avg_logprob, compression_ratio=compression_ratio)


def test_normal_speech_is_kept():
    assert keep_segment(**seg("The Lord is my shepherd.")) is True


def test_high_no_speech_probability_is_dropped():
    assert keep_segment(**seg("Thank you.", no_speech_prob=0.85)) is False


def test_low_confidence_is_dropped():
    # Boundary moved -1.6 -> -2.4 on 2026-09-20: at -1.6 this was also throwing
    # away real accented speech. See AVG_LOGPROB_MIN in config.py for the sweep.
    assert keep_segment(**seg("Veghurt's costave", avg_logprob=-2.4)) is False


def test_repetition_loop_is_dropped():
    """'eh, eh, eh, eh' compresses far better than real speech."""
    assert keep_segment(**seg("eh, eh, eh, eh, eh.", compression_ratio=3.1)) is False


def test_youtube_outro_is_dropped_even_when_confident():
    """Whisper emits these with high confidence; they are never church speech."""
    assert keep_segment(**seg("Thanks for watching!")) is False
    assert keep_segment(**seg("Please subscribe to my channel.")) is False


def test_blocklist_match_ignores_case_and_punctuation():
    assert keep_segment(**seg("thanks for watching")) is False


def test_strip_hallucinations_joins_surviving_segments():
    segments = [seg("He restores my soul."), seg("Thanks for watching!"),
                seg("Amen.")]
    assert strip_hallucinations(segments) == "He restores my soul. Amen."


def test_strip_hallucinations_returns_empty_when_all_dropped():
    assert strip_hallucinations([seg("Thank you.", no_speech_prob=0.9)]) == ""


# --- Non-lexical fillers ----------------------------------------------------
# After the audio stopped on 2026-08-21 the pipeline produced 8 phantom
# segments in 74 seconds — "Ugh." x2, "Yeah.", "Mm.", "Thank you." x3,
# "Oh. Good." — and translated every one of them onto the screens.

def test_standalone_filler_interjection_is_dropped():
    """A segment that is nothing but a grunt has nothing to translate."""
    assert keep_segment(**seg("Ugh.")) is False
    assert keep_segment(**seg("Mm.")) is False
    assert keep_segment(**seg("Yeah.")) is False
    assert keep_segment(**seg("Uh huh.")) is False


def test_filler_leading_real_speech_is_kept():
    """Only whole-segment fillers go; a real sentence keeps its false start."""
    assert keep_segment(**seg("Uh, turn with me to Ephesians two.")) is True
    assert keep_segment(**seg("Yeah, that is exactly the point.")) is True


def test_amen_and_hallelujah_are_not_filler():
    """Short, but the congregation means them."""
    assert keep_segment(**seg("Amen.")) is True
    assert keep_segment(**seg("Hallelujah!")) is True


def test_bare_thank_you_is_still_kept():
    """config.py deliberately excludes it: a preacher genuinely says it."""
    assert keep_segment(**seg("Thank you.")) is True


# --- Accented speech is low-confidence speech, not hallucination -------------
# Measured 2026-09-20 on 120 NPTEL clips (Indian-English lectures), running the
# real WhisperSTT. AVG_LOGPROB_MIN = -1.0 was discarding 364 of the 514 words
# the model produced -- 71% of real content -- because an accent lowers
# confidence exactly the way a hallucination does. Corpus WER against the
# reference, sweeping the threshold and leaving every other filter untouched:
#
#   -0.8   91.8%      -1.4   70.3%      -2.0   65.9%
#   -1.0   85.6%  <-- was   -1.6   67.7%      -3.0   65.5%
#   -1.2   77.0%      -1.8   66.1%      off    65.5%
#
# Monotonic: the threshold never once paid for itself. And it is not what
# guards against noise — fed digital silence, fan noise at the measured floor
# (RMS 3806) and white hiss, the model emits NOTHING, because Silero VAD stops
# it long before any score is read. Nor did it catch the hallucination actually
# observed in this church: "Thank you." 39 times on 2026-08-23, which is
# high-confidence and sailed through.
#
# -2.0 rather than off: it captures ~97% of the available gain and still floors
# total garbage, for conditions no synthetic noise reproduces (a distant
# conversation, music through a wall).

def test_accented_speech_is_not_mistaken_for_hallucination():
    """Real sentences from the measured run, at the confidence an accent produces."""
    assert keep_segment(**seg("if you remember right in the first week",
                              avg_logprob=-1.134)) is True
    assert keep_segment(**seg("do at the end of this course",
                              avg_logprob=-1.071)) is True
    assert keep_segment(**seg("to be noted", avg_logprob=-1.07)) is True



def test_repetition_is_caught_by_compression_not_confidence():
    """The garbage the old threshold caught has its own, better-aimed filter.

    "is legally is legally is legally" scored -1.338 and was dropped for
    confidence. Its compression ratio condemns it on its own merits, so
    relaxing the confidence floor does not let it back in.
    """
    assert keep_segment(**seg("is legally is legally is legally is legally",
                              avg_logprob=-1.338, compression_ratio=2.6)) is False


def test_hotwords_and_prompt_are_never_sent_together(monkeypatch):
    """Measured 2026-09-20 on clips the tee captured from a live service:
    sending hotwords AND initial_prompt together overflows Whisper's prompt
    budget and the decoder loops — 42 stutters per 100 words, where either one
    alone scored zero. Nothing raises, so it is pinned here."""
    import stt

    monkeypatch.setattr(stt, "WHISPER_HOTWORDS", "Ephesians Hosanna")
    monkeypatch.setattr(stt, "WHISPER_INITIAL_PROMPT", "Good morning, church.")
    assert stt._hotwords() is None

    # Either one alone was measured clean, so neither is suppressed alone.
    monkeypatch.setattr(stt, "WHISPER_INITIAL_PROMPT", None)
    assert stt._hotwords() == "Ephesians Hosanna"


def test_bare_you_from_silence_is_dropped():
    """Measured 2026-09-20: with WHISPER_VAD_FILTER off, silence decodes to a
    bare "you" with no_speech_prob=0.00 and avg_logprob=-0.63 — confident on
    every numeric signal, so only a whole-segment word test catches it."""
    assert not keep_segment("you", 0.0, -0.63, 0.27)
    assert not keep_segment("You.", 0.0, -0.63, 0.27)


def test_those_words_inside_a_real_sentence_survive():
    """The reason this is not in HALLUCINATION_PHRASES: that list also matches
    on startswith, and would take Matthew 5:14 with it."""
    assert keep_segment("You are the light of the world.", 0.1, -0.4, 1.5)
    # And "Thank you." stays kept — see test_bare_thank_you_is_still_kept.
    assert keep_segment("Thank God for His mercy.", 0.1, -0.4, 1.5)
