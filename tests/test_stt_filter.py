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
    assert keep_segment(**seg("Veghurt's costave", avg_logprob=-1.6)) is False


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
