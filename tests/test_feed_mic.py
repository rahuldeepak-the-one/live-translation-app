"""The only microphone ingress there is, so its silent failures get a test.

Silent in the sense that matters on a Sunday: captions keep appearing and
nobody can tell from the outside that anything is wrong.

  wrong channel   A single mic on channel 1 of the MG-XU reaches the USB bus on
                  the LEFT only. Averaging L+R then puts the speech 6 dB down
                  with a dead channel's converter noise folded in — quiet,
                  noisier audio that Whisper transcribes badly rather than not
                  at all.
  stale channel   The control room can change device mid-service. The side that
                  carried the signal on the mixer says nothing about the side
                  that carries it on the next device.
  a monitor source  ".monitor" sources loop back whatever the speakers are
                  playing. Picking one feeds the PA's own output into the
                  transcriber, which transcribes fluently and means nothing.
"""
import array
import importlib.util
import types
from pathlib import Path

import pytest

# feed-mic.py is a command first and a module second, and the hyphen it is
# spelled with is not importable. Loading it by path keeps the script's name
# the thing the operator types.
_spec = importlib.util.spec_from_file_location(
    "feed_mic", Path(__file__).resolve().parent.parent / "feed-mic.py")
feed_mic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(feed_mic)

RATE = feed_mic.RATE
# What one read() consumes, in stereo frames — see Capture.read.
FRAMES_PER_CHUNK = RATE * feed_mic.CHUNK_MS // 1000


def stereo(left_amp, right_amp, frames=FRAMES_PER_CHUNK):
    """Interleaved s16le, the way parec delivers it."""
    out = array.array("h")
    for _ in range(frames):
        out.extend((left_amp, right_amp))
    return out.tobytes()


# open() spawns parec; nothing here wants a real one.
FAKE_PAREC = types.SimpleNamespace(
    stdout=None, terminate=lambda: None,
    wait=lambda timeout=None: None, kill=lambda: None)


def make_capture(channel="auto"):
    return feed_mic.Capture({"name": "src", "description": "Mixer"}, channel)


def feed(cap, left_amp, right_amp, chunks=1):
    """Push chunks through the real read() path, guard and all."""
    mono, peak = None, 0
    for _ in range(chunks):
        mono, peak = cap.read(lambda n: stereo(left_amp, right_amp))
    return mono, peak


# Ten chunks is one second, which is what _decide waits for.
CHUNKS_TO_DECIDE = RATE // FRAMES_PER_CHUNK


def test_a_mixer_sending_on_one_side_only_uses_that_side():
    """The -6 dB trap. Left carries speech, right carries converter noise."""
    cap = make_capture()
    feed(cap, 8000, 50, CHUNKS_TO_DECIDE)
    assert cap.channel == "l"

    cap = make_capture()
    feed(cap, 50, 8000, CHUNKS_TO_DECIDE)
    assert cap.channel == "r"


def test_the_chosen_side_is_what_actually_gets_sent():
    """Deciding "l" and then averaging anyway would be the same 6 dB loss with
    a reassuring log line over it."""
    cap = make_capture()
    feed(cap, 8000, 50, CHUNKS_TO_DECIDE)
    mono, peak = feed(cap, 8000, 50)
    assert peak == 8000
    assert set(array.array("h", mono)) == {8000}


def test_a_real_stereo_pair_is_averaged():
    cap = make_capture()
    feed(cap, 8000, 7000, CHUNKS_TO_DECIDE)
    assert cap.channel == "mix"
    mono, _ = feed(cap, 8000, 7000)
    assert set(array.array("h", mono)) == {7500}


def test_a_dead_pair_is_averaged_rather_than_guessed():
    """Silence on both sides must not be read as 'one side is dead'. Deciding
    from a lull would pin the wrong channel for the rest of the service."""
    cap = make_capture()
    feed(cap, 0, 0, CHUNKS_TO_DECIDE)
    assert cap.channel == "mix"


def test_the_decision_waits_for_a_full_second():
    """Deciding from the first chunk would pin the channel on whichever side a
    consonant happened to land in."""
    cap = make_capture()
    feed(cap, 8000, 50, CHUNKS_TO_DECIDE - 1)
    assert cap._decided is False
    assert cap.channel == "auto"


def test_the_decision_is_made_once_and_then_stands():
    """Re-deciding per chunk flips mid-sentence every time the speaker pauses,
    which is audible as a jump in level."""
    cap = make_capture()
    feed(cap, 8000, 50, CHUNKS_TO_DECIDE)
    assert cap.channel == "l"
    feed(cap, 50, 8000, CHUNKS_TO_DECIDE * 2)
    assert cap.channel == "l"


def test_an_explicit_channel_is_never_second_guessed():
    cap = make_capture(channel="r")
    feed(cap, 8000, 50, CHUNKS_TO_DECIDE)
    assert cap.channel == "r"


def test_switching_device_re_probes_the_channel(monkeypatch):
    """/status/<token> switches capture mid-service by calling open() on the
    new source. A decision carried over from the previous device is the 6 dB
    loss again, on a device nobody chose it for."""
    monkeypatch.setattr(feed_mic.subprocess, "Popen", lambda *a, **k: FAKE_PAREC)
    cap = make_capture()
    feed(cap, 8000, 50, CHUNKS_TO_DECIDE)
    assert cap.channel == "l"

    cap.source = {"name": "other", "description": "Built-in"}
    cap.open()
    assert cap._decided is False
    feed(cap, 50, 8000, CHUNKS_TO_DECIDE)
    assert cap.channel == "r"


def test_switching_device_does_not_override_an_explicit_channel(monkeypatch):
    monkeypatch.setattr(feed_mic.subprocess, "Popen", lambda *a, **k: FAKE_PAREC)
    cap = make_capture(channel="l")
    cap.open()
    assert cap.channel == "l"
    assert cap._decided is True


@pytest.mark.parametrize("source", [
    {"name": "alsa_output.usb-Yamaha.monitor", "properties": {}},
    {"name": "alsa_output.pci-0000_00.analog-stereo.monitor", "properties": {}},
    {"name": "alsa_input.real", "properties": {"device.class": "monitor"}},
])
def test_monitor_sources_never_enter_the_running(source, monkeypatch):
    monkeypatch.setattr(feed_mic, "pactl_json", lambda *a: [source])
    assert feed_mic.input_sources() == []


BUILTIN = {"name": "alsa_input.pci-0000_00.analog-stereo",
           "description": "Built-in Audio",
           "properties": {"device.bus": "pci"}}
MIXER = {"name": "alsa_input.usb-Yamaha_MG-XU",
         "description": "Yamaha MG-XU",
         "properties": {"device.bus": "usb"}}


def test_a_plugged_in_device_outranks_the_built_in_microphone(monkeypatch):
    """Nobody plugs a mixer in and means to record the laptop's own mic — the
    one being deafened by the fans this app just spun up."""
    monkeypatch.setattr(feed_mic, "pactl_json", lambda *a: [BUILTIN, MIXER])
    assert feed_mic.pick_source("")["name"] == MIXER["name"]


def test_a_named_device_wins_over_the_plugged_in_rule(monkeypatch):
    monkeypatch.setattr(feed_mic, "pactl_json", lambda *a: [BUILTIN, MIXER])
    assert feed_mic.pick_source("built-in")["name"] == BUILTIN["name"]


def test_the_default_url_follows_whether_a_certificate_exists(monkeypatch):
    """A wss:// default against a certless server is a connection failure with
    nothing on screen explaining it — and since the browser left, a certless
    server is a supported way to run."""
    monkeypatch.setattr(feed_mic.tls, "enabled", lambda: False)
    assert feed_mic.default_url().startswith("ws://")
    monkeypatch.setattr(feed_mic.tls, "enabled", lambda: True)
    assert feed_mic.default_url().startswith("wss://")
