"""Reading a microphone's level in the units the pipeline actually branches on.

The rule these tests exist to protect: a noise floor ABOVE SILENCE_THRESHOLD is
the worst outcome and must be reported first, even when speech is also present
and loud. That combination looks healthy by every other measure — the meter
moves, words are transcribed — while trailing silence is never detected and
sentences stop being cut. A "peak is fine" verdict there would be actively
misleading.
"""
import array

import pytest

from config import SAMPLE_RATE, SILENCE_THRESHOLD, SPEECH_WINDOW_S
from miclevel import (
    HEADROOM_FACTOR, percentile, report, split_channels, verdict, window_rms,
)


def interleave(left, right):
    """Stereo s16le bytes, the way parec delivers them."""
    out = array.array("h")
    for a, b in zip(left, right):
        out.extend((int(a), int(b)))
    return out.tobytes()


def tone(amplitude, seconds=1.0):
    """A square wave: its RMS is exactly `amplitude`, so assertions are exact."""
    n = int(SAMPLE_RATE * seconds)
    return [amplitude if i % 2 else -amplitude for i in range(n)]


def levels(notes):
    return [level for level, _ in notes]


def test_split_channels_deinterleaves():
    pcm = interleave([100, 200, 300], [-1, -2, -3])
    left, right = split_channels(pcm)
    assert list(left) == [100, 200, 300]
    assert list(right) == [-1, -2, -3]


def test_split_channels_ignores_a_truncated_final_frame():
    """parec is killed mid-frame by head -c, so odd trailing bytes are normal."""
    pcm = interleave([100], [200]) + b"\x01"
    left, right = split_channels(pcm)
    assert list(left) == [100] and list(right) == [200]


def test_window_rms_recovers_the_amplitude():
    assert window_rms(tone(500))[0] == pytest.approx(500, rel=1e-6)


def test_window_rms_slices_at_the_pipelines_own_window():
    rms = window_rms(tone(500, seconds=1.0))
    assert len(rms) == int(1.0 / SPEECH_WINDOW_S)


def test_window_rms_does_not_average_a_short_burst_away():
    """The reason audio_buffer.py checks per-window instead of over the whole
    buffer: one loud window inside a quiet second must still show up."""
    quiet = tone(10, seconds=0.9)
    loud = tone(5000, seconds=0.1)
    assert max(window_rms(quiet + loud)) == pytest.approx(5000, rel=1e-6)


def test_a_floor_above_the_threshold_is_reported_even_when_speech_is_loud():
    notes = verdict(floor=SILENCE_THRESHOLD + 1, peak=20000,
                    left_peak=20000, right_peak=20000, phase="speech")
    assert levels(notes) == ["bad"]
    assert "stop cutting" in notes[0][1]


def test_a_quiet_floor_is_the_right_answer_during_the_floor_phase():
    notes = verdict(floor=50, peak=120, left_peak=120, right_peak=120,
                    phase="floor")
    assert levels(notes) == ["ok"]


def test_silence_while_speaking_is_a_fault():
    notes = verdict(floor=5, peak=SILENCE_THRESHOLD - 1,
                    left_peak=10, right_peak=10, phase="speech")
    assert levels(notes) == ["bad"]
    assert "fader" in notes[0][1]


def test_a_hard_panned_mic_is_flagged():
    notes = verdict(floor=20, peak=SILENCE_THRESHOLD * 10,
                    left_peak=8000, right_peak=20, phase="speech")
    assert "warn" in levels(notes)
    assert any("right channel" in message for _, message in notes)


def test_a_centred_mic_is_not_flagged_as_panned():
    notes = verdict(floor=20, peak=SILENCE_THRESHOLD * 10,
                    left_peak=8000, right_peak=7000, phase="speech")
    assert not any("channel" in message for _, message in notes)


def test_speech_barely_over_the_threshold_warns_rather_than_passing():
    notes = verdict(floor=20, peak=SILENCE_THRESHOLD * HEADROOM_FACTOR - 1,
                    left_peak=1000, right_peak=1000, phase="speech")
    assert levels(notes) == ["warn"]
    assert "quiet passages" in notes[0][1]


def test_a_healthy_capture_passes():
    notes = verdict(floor=20, peak=SILENCE_THRESHOLD * HEADROOM_FACTOR + 1,
                    left_peak=1300, right_peak=1300, phase="speech")
    assert levels(notes) == ["ok"]


def test_percentile_does_not_run_off_the_end():
    assert percentile([1.0], 100) == 1.0
    assert percentile([1.0, 2.0, 3.0], 0) == 1.0


def test_report_rejects_a_capture_too_short_to_judge():
    assert report(interleave(tone(500, 0.1), tone(500, 0.1)), "speech") == 1


def test_report_exit_code_follows_the_verdict():
    # Speech is bursts over a quiet floor, not a continuous tone — a constant
    # signal loud enough to pass as speech IS a floor above the threshold, and
    # the module is right to fail it.
    speech = (tone(10, seconds=0.5)
              + tone(SILENCE_THRESHOLD * HEADROOM_FACTOR + 100, seconds=0.5))
    assert report(interleave(speech, speech), "speech") == 0

    hissing = tone(SILENCE_THRESHOLD + 100, seconds=1.0)
    assert report(interleave(hissing, hissing), "floor") == 1
