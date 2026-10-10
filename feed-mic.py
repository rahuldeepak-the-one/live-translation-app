#!/usr/bin/env python3
# ============================================================
# THE microphone ingress. parec -> /ws/mic. There is no other one.
#
# WHY NO BROWSER: static/mic.js used to hold this job and was deleted with it.
# A capture page cost more than it earned:
#
#   * getUserMedia's noiseSuppression and autoGainControl default ON, and both
#     are wrong for a mixer feed carrying a preacher through a PA. AGC winds
#     the gain up into room tone between sentences and slams it down when
#     speech returns; noise suppression is trained on steady broadband noise,
#     which is what a consonant and a sentence ending look like. Whisper sees a
#     signal that swells, fades and loses its endings, and does what it does
#     with ambiguous audio: it repeats. mic.js had eventually turned both off,
#     but only after a service had been lost to them.
#   * The page records whatever device it is pointed at, and getting that
#     pointing right needed two shell scripts (check-mic.sh, use-mic.sh) that
#     steered the SYSTEM DEFAULT input and warned for thirty lines about
#     Chrome's per-site device binding. All three are now deleted: this script
#     opens a named pactl source directly and reports it to the control room.
#   * There is no tab here to background, lock, or close by accident.
#
# parec hands over precisely what the mixer sent — 16 kHz signed 16-bit PCM,
# no DSP anywhere in the chain.
#
# The protocol is unchanged, so /status/<token> still drives this: it sends
# start / stop / set_device, and the mic_state below is what it renders.
#
# See also: miclevel.py — the level question that matters is not dBFS but
# whether the floor sits under config.SILENCE_THRESHOLD. Pipe a capture into
# it:  parec --device <name> --raw --format=s16le --rate=16000 --channels=1 \
#            | python3 miclevel.py
# ============================================================
import argparse
import array
import asyncio
import json
import math
import ssl
import subprocess
import sys
import time

import websockets

import tls
from config import PORT

RATE = 16000          # server-side SAMPLE_RATE; parec resamples to meet it
CHUNK_MS = 100
STATE_EVERY_S = 0.3   # how often the operator's level meter is refreshed
FULL_SCALE = 32768.0


# ------------------------------------------------------------
# Device discovery. pactl's JSON, not its human-readable output: that reflows
# between versions, the JSON does not.
# ------------------------------------------------------------
def pactl_json(*args):
    out = subprocess.run(("pactl", "-f", "json") + args,
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.stderr.write(out.stderr)
        sys.exit(2)
    return json.loads(out.stdout or "[]")


def input_sources():
    """Real capture devices. A ".monitor" source loops back whatever the
    speakers are playing, so selecting one would feed the PA into the
    transcriber — they never enter the running."""
    return [s for s in pactl_json("list", "sources")
            if s.get("properties", {}).get("device.class") != "monitor"
            and not s["name"].endswith(".monitor")]


def describe(src):
    return src.get("description") or src["name"]


def pick_source(pattern):
    sources = input_sources()
    if not sources:
        sys.exit("No capture devices at all. Is anything plugged in?")
    if pattern:
        matches = [s for s in sources
                   if pattern.lower() in s["name"].lower()
                   or pattern.lower() in describe(s).lower()]
        if not matches:
            print("Nothing matches %r. Available inputs:" % pattern)
            for s in sources:
                print("   %s\n     %s" % (describe(s), s["name"]))
            sys.exit(2)
        return matches[0]
    # No pattern: somebody who plugged a device in meant to use it, so anything
    # not on the PCI bus outranks the built-in microphone.
    external = [s for s in sources
                if s.get("properties", {}).get("device.bus") != "pci"]
    return (external or sources)[0]


# ------------------------------------------------------------
# Capture
# ------------------------------------------------------------
class Capture:
    """parec, plus the channel decision.

    Captured in STEREO on purpose even though the server wants mono. A single
    mic on channel 1 of the MG-XU reaches the USB bus on the LEFT only, and the
    obvious `parec --channels=1` averages the two — putting the speech 6 dB
    down with a dead channel's noise folded in. That -6 dB was invisible from
    a browser. Here we look at both channels and take the one with signal.
    """

    def __init__(self, source, channel="auto", gain=1.0):
        self.source = source
        # What the operator asked for, kept apart from what we decided: only an
        # "auto" request may be re-probed when the device changes under us.
        self.requested = channel
        self.gain = gain
        self.proc = None
        self._reset_probe()

    def _reset_probe(self):
        self.channel = self.requested
        self._decided = self.requested != "auto"
        self._probe_l = 0.0
        self._probe_r = 0.0
        self._probe_n = 0

    def open(self):
        # Re-probe on every open. The control room can switch devices
        # mid-service (/status/<token>), and the channel that carried the
        # signal on the mixer says nothing about the one on the laptop's own
        # input — keeping the old answer takes the 6 dB hit this class exists
        # to avoid, on a device nobody chose it for.
        self._reset_probe()
        self.close()
        self.proc = subprocess.Popen(
            ["parec", "--device", self.source["name"], "--raw",
             "--format=s16le", "--rate=%d" % RATE, "--channels=2"],
            stdout=subprocess.PIPE)

    def close(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def _decide(self, left, right):
        """Pick the live channel once, from the first second of audio, then
        stay there. Deciding per chunk would flip mid-sentence whenever the
        speaker paused, which is audible as a jump in level."""
        self._probe_l += sum(abs(x) for x in left)
        self._probe_r += sum(abs(x) for x in right)
        self._probe_n += len(left)
        if self._probe_n < RATE:            # keep probing for ~1s
            return
        self._decided = True
        # A dead channel still carries a little converter noise, so require a
        # clear difference before calling one side dead; otherwise the board is
        # sending a real stereo pair and averaging is correct.
        louder, quieter = sorted((self._probe_l, self._probe_r), reverse=True)
        if quieter > 0 and louder / max(quieter, 1.0) > 3.0:
            self.channel = "l" if self._probe_l > self._probe_r else "r"
            print("   channel: mixer is sending on %s only — using it "
                  "(averaging would cost 6 dB)" % self.channel.upper())
        else:
            self.channel = "mix"
            print("   channel: real stereo pair — averaging L+R")

    def read(self, executor_read):
        """Return (pcm_bytes, peak) for one chunk, or (None, 0) at EOF."""
        raw = executor_read(RATE * 2 * 2 * CHUNK_MS // 1000)
        if not raw:
            return None, 0
        frames = array.array("h")
        frames.frombytes(raw[:len(raw) // 4 * 4])
        left, right = frames[0::2], frames[1::2]

        if not self._decided:
            self._decide(left, right)

        if self.channel == "l":
            mono = left
        elif self.channel == "r":
            mono = right
        elif self.channel == "mix":
            mono = array.array("h", [(a + b) // 2 for a, b in zip(left, right)])
        else:                                # still probing — average for now
            mono = array.array("h", [(a + b) // 2 for a, b in zip(left, right)])

        if self.gain != 1.0:
            mono = array.array("h", [
                max(-32768, min(32767, int(x * self.gain))) for x in mono])

        peak = max(max(mono), -min(mono)) if mono else 0
        return mono.tobytes(), peak


# ------------------------------------------------------------
# The socket
# ------------------------------------------------------------
async def run(args):
    ctx = ssl.create_default_context()
    if args.insecure:
        # The server's certificate is self-signed by ./make-cert.sh, and we are
        # talking to this same machine over the loopback interface.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    ssl_ctx = ctx if args.url.startswith("wss://") else None

    source = pick_source(args.device)
    print("Feeding from: %s" % describe(source))
    print("   %s" % source["name"])
    print("   -> %s" % args.url)
    if args.gain != 1.0:
        print("   gain: x%.2f" % args.gain)
    print()

    devices = [{"id": s["name"], "label": describe(s)} for s in input_sources()]

    while True:                                     # reconnect forever
        cap = Capture(source, args.channel, args.gain)
        try:
            async with websockets.connect(args.url, ssl=ssl_ctx,
                                          max_size=None) as ws:
                print("Connected. Ctrl-C to stop.")
                cap.open()
                await pump(ws, cap, devices, args)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print("\nLost the server (%s) — retrying in 2s" % exc)
            await asyncio.sleep(2)
        finally:
            cap.close()


async def pump(ws, cap, devices, args):
    loop = asyncio.get_running_loop()
    capturing = True

    def blocking_read(n):
        return cap.proc.stdout.read(n)

    async def send_state(level):
        await ws.send(json.dumps({
            "type": "mic_state",
            "capturing": capturing,
            "deviceId": cap.source["name"],
            "deviceLabel": describe(cap.source),
            "devices": devices,
            "level": level,
        }))

    async def discard_partial():
        """Drop whatever half-utterance the server is still holding.

        Every branch below breaks the audio stream, and the server's
        AudioBuffer does not know that: without this it splices the samples
        from before the break onto the ones after, and transcribes the join as
        one utterance. mic.js sent this on the same three transitions.
        """
        await ws.send(json.dumps({"type": "clear"}))

    async def listen():
        """Honour the control room. It cannot open a device for us, so it asks
        and we obey."""
        nonlocal capturing
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if msg.get("type") != "command":
                continue
            action = msg.get("action")
            if action == "stop":
                capturing = False
                await discard_partial()
                print("\nStopped from the control room.")
            elif action == "start":
                capturing = True
                await discard_partial()
                print("\nStarted from the control room.")
            elif action == "set_device":
                wanted = next((s for s in input_sources()
                               if s["name"] == msg.get("deviceId")), None)
                if wanted:
                    cap.source = wanted
                    cap.open()
                    await discard_partial()
                    print("\nSwitched to %s" % describe(wanted))
            await send_state(0.0)

    async def feed():
        await ws.send(json.dumps({"type": "config", "sampleRate": RATE}))
        last_state = 0.0
        window_peak = 0
        while True:
            chunk, peak = await loop.run_in_executor(
                None, cap.read, blocking_read)
            if chunk is None:
                raise RuntimeError("parec ended — was the mixer unplugged?")
            window_peak = max(window_peak, peak)
            # A stopped feed still drains parec above: PulseAudio would
            # otherwise buffer, and restarting would replay stale audio.
            if capturing:
                await ws.send(chunk)
            now = time.monotonic()
            if now - last_state >= STATE_EVERY_S:
                level = min(1.0, window_peak / FULL_SCALE)
                await send_state(level)
                meter(window_peak, capturing)
                window_peak = 0
                last_state = now

    await asyncio.gather(listen(), feed())


def meter(peak, capturing):
    bars = int(min(1.0, peak / FULL_SCALE) ** 0.35 * 30)
    dbfs = "%6.1f dBFS" % (20 * math.log10(peak / FULL_SCALE)) if peak else "  -inf"
    flag = "" if capturing else "  [STOPPED]"
    sys.stdout.write("\r  peak %5d  %s  |%s%s|%s" %
                     (peak, dbfs, "#" * bars, "-" * (30 - bars), flag))
    sys.stdout.flush()


def default_url():
    scheme = "wss" if tls.enabled() else "ws"
    return "%s://127.0.0.1:%d/ws/mic" % (scheme, PORT)


def main():
    p = argparse.ArgumentParser(
        description="Feed a microphone into the translator's /ws/mic. This is "
                    "the only capture path; there is no browser page.")
    p.add_argument("device", nargs="?", default="",
                   help="part of the device name, e.g. 'yamaha' or 'mg-xu'. "
                        "Default: any non-built-in input.")
    # Read from the certificate, not hardcoded: since the browser left, the
    # server boots happily on plain HTTP, and a wss:// default would then fail
    # to connect with nothing on screen explaining why.
    p.add_argument("--url", default=default_url(),
                   help="capture socket (default: %(default)s)")
    p.add_argument("--gain", type=float, default=1.0,
                   help="multiply the signal before sending. The board's knob "
                        "is always the better place to fix level; this is for "
                        "when you cannot reach it mid-service.")
    p.add_argument("--channel", choices=("auto", "l", "r", "mix"),
                   default="auto",
                   help="which side of the USB pair carries the mic "
                        "(default: detect from the first second)")
    p.add_argument("--secure", dest="insecure", action="store_false",
                   help="verify the server certificate (it is self-signed by "
                        "./make-cert.sh, so this fails by default)")
    args = p.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
