"""Is this microphone's level right for the pipeline?

Reads raw s16le PCM on stdin and reports it in the ONLY units that matter here:
the int16 RMS that audio_buffer.py compares against SILENCE_THRESHOLD. A level
meter in dBFS looks professional and answers the wrong question — what decides
whether this app works is whether the noise floor sits below that constant and
speech rises above it.

The two failure modes are opposites, and confusing them wastes a Sunday:

  floor too HIGH   has_speech() is true forever, trailing silence is never
                   detected, and sentences stop being cut. Everything still
                   looks fine — captions appear, they just never end.
  peak too LOW     speech is read as silence and nothing is transcribed at all.

Only the second one is obvious from the outside, which is why the first gets
the louder warning.

Stdlib only, deliberately: you reach for this before anyone has necessarily
built the venv, and diagnosing the microphone is exactly what you want to do
when the rest of the stack is not up yet. Feed it parec directly — STEREO,
because the two channels are reported separately, and bounded, because this
reads stdin to EOF:

    timeout 8 parec --device <name> --raw --format=s16le --rate=16000 \
        --channels=2 | python3 miclevel.py [floor|speech]

feed-mic.py's own meter answers a different question (dBFS peak, for setting
the board's knob); this one answers whether the pipeline will work.
"""
import array
import math
import sys
import textwrap

from config import SAMPLE_RATE, SILENCE_THRESHOLD, SPEECH_WINDOW_S

# Two channels of int16. Captured in stereo even though the pipeline is mono,
# because a single mic on channel 1 of a mixer reaches the USB bus on the left
# only — and Chrome asks for channelCount:1, so it averages the pair and the
# signal arrives ~6 dB down with a dead channel's noise folded in. That is
# invisible in the browser and obvious the moment you look at the channels.
FRAME_BYTES = 4

# Below this a capture is too short to say anything useful about.
MIN_SECONDS = 0.25

# A peak this many times the threshold is "clearing it with headroom". Under
# it, normal variation in how loudly someone speaks will dip below the line.
HEADROOM_FACTOR = 4

# One channel this much louder than the other means the source is panned hard,
# not merely off-centre.
PAN_RATIO = 4


def split_channels(pcm_bytes):
    """(left, right) int16 sequences from interleaved stereo."""
    usable = len(pcm_bytes) // FRAME_BYTES * FRAME_BYTES
    samples = array.array("h")
    samples.frombytes(pcm_bytes[:usable])
    return samples[0::2], samples[1::2]


def window_rms(channel):
    """RMS per SPEECH_WINDOW_S window — the same slicing audio_buffer.py uses.

    Per-window rather than over the whole capture because a short sentence
    inside a long silence averages down below the threshold, which is the exact
    mistake has_speech() was written to avoid.
    """
    step = max(1, int(SPEECH_WINDOW_S * SAMPLE_RATE))
    n = len(channel) // step * step
    out = [math.sqrt(sum(float(x) * x for x in channel[i:i + step]) / step)
           for i in range(0, n, step)]
    return out or [0.0]


def percentile(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p / 100))]


def verdict(floor, peak, left_peak, right_peak, phase):
    """[(level, message)] — what these numbers mean for the pipeline.

    `phase` is "floor" when nobody is meant to be speaking, so a silent capture
    is the correct answer rather than a fault.
    """
    notes = []

    if floor >= SILENCE_THRESHOLD:
        return [("bad",
                 f"floor {floor:.0f} is ABOVE SILENCE_THRESHOLD "
                 f"({SILENCE_THRESHOLD}) — has_speech() will be true forever, "
                 f"trailing silence is never seen, and sentences stop cutting. "
                 f"Bring the board's gain down.")]

    if phase == "floor":
        return [("ok", f"floor {floor:.0f}, comfortably under "
                       f"{SILENCE_THRESHOLD} — silence reads as silence.")]

    if peak < SILENCE_THRESHOLD:
        return [("bad",
                 f"nothing crossed {SILENCE_THRESHOLD} while you were speaking "
                 f"(peak {peak:.0f}) — the fader is down, the channel is muted, "
                 f"or this is the wrong input.")]

    loud, quiet = max(left_peak, right_peak), min(left_peak, right_peak)
    if loud and loud / max(1.0, quiet) > PAN_RATIO:
        side = "right" if left_peak > right_peak else "left"
        notes.append(("warn",
                      f"the {side} channel is nearly silent, so this mic is on "
                      f"one side of the mixer only. Chrome averages both "
                      f"channels, costing ~6 dB. Pan it to centre on the board."))

    if peak < SILENCE_THRESHOLD * HEADROOM_FACTOR:
        notes.append(("warn",
                      f"peak {peak:.0f} clears {SILENCE_THRESHOLD}, but not by "
                      f"much — quiet passages will be read as silence. More "
                      f"gain on the board would help."))
    else:
        notes.append(("ok", f"floor {floor:.0f}, peak {peak:.0f} — speech "
                            f"clears {SILENCE_THRESHOLD} with headroom."))
    return notes


def report(pcm_bytes, phase):
    """Print the per-channel table and the verdict. Returns a shell exit code."""
    if len(pcm_bytes) < SAMPLE_RATE * FRAME_BYTES * MIN_SECONDS:
        print("   no data — device busy, unplugged, or not delivering samples.")
        return 1

    left, right = split_channels(pcm_bytes)
    mono = [(a + b) / 2 for a, b in zip(left, right)]
    lanes = [("ch1 (L)", window_rms(left)),
             ("ch2 (R)", window_rms(right)),
             ("mono", window_rms(mono))]

    for name, rms in lanes:
        print(f"   {name:<9} floor {percentile(rms, 10):6.0f}   "
              f"median {percentile(rms, 50):6.0f}   peak {max(rms):6.0f}")
    print()

    mono_rms = lanes[2][1]
    notes = verdict(percentile(mono_rms, 10), max(mono_rms),
                    max(lanes[0][1]), max(lanes[1][1]), phase)

    icon = {"ok": "✅", "warn": "⚠️ ", "bad": "❌"}
    for level, message in notes:
        body = textwrap.wrap(message, width=66)
        print(f"   {icon[level]} {body[0]}")
        for line in body[1:]:
            print(f"      {line}")
    return 0 if all(level == "ok" for level, _ in notes) else 1


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "speech"
    sys.exit(report(sys.stdin.buffer.read(), phase))
