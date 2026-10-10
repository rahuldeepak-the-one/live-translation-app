"""Broadcast hub — pushes caption events to every connected screen."""
import logging
from collections import deque

from config import HISTORY_SIZE
from display_state import initial_state, validate

logger = logging.getLogger(__name__)

MAX_DEVICES = 24          # a machine with more inputs than this is not a church
MAX_LABEL = 120


def initial_mic_state():
    """What the operator sees before any capture page has ever checked in."""
    return {"connected": False, "capturing": False, "deviceId": None,
            "deviceLabel": None, "devices": [], "level": 0.0}


def clean_mic_state(raw):
    """Shape-check a state reported by the capture page.

    /ws/mic carries no token — anything on the church WiFi can open it and say
    whatever it likes. That cannot be fixed here (the same socket already
    accepts audio), but it does mean this state reaches an operator's screen
    unvetted, so every field is clamped rather than trusted. Raises ValueError
    on anything that is not an object at all.
    """
    if not isinstance(raw, dict):
        raise ValueError("mic state must be an object")

    devices = []
    for entry in (raw.get("devices") or [])[:MAX_DEVICES]:
        if not isinstance(entry, dict):
            continue
        device_id, label = entry.get("id"), entry.get("label")
        if not isinstance(device_id, str) or not isinstance(label, str):
            continue
        devices.append({"id": device_id[:MAX_LABEL], "label": label[:MAX_LABEL]})

    # bool is an int subclass, so it would sail through a plain isinstance check
    # and clamp to 1.0 — a muted mic reading "full scale".
    level = raw.get("level", 0)
    if isinstance(level, bool) or not isinstance(level, (int, float)):
        level = 0.0

    def text(value):
        return value[:MAX_LABEL] if isinstance(value, str) else None

    return {
        "connected": True,
        "capturing": bool(raw.get("capturing")),
        "deviceId": text(raw.get("deviceId")),
        "deviceLabel": text(raw.get("deviceLabel")),
        "devices": devices,
        "level": max(0.0, min(1.0, float(level))),
    }


class BroadcastHub:
    def __init__(self, history_size=HISTORY_SIZE):
        self._clients = set()
        self._history = deque(maxlen=history_size)
        # Authoritative wall configuration. Retained so a screen that drops
        # WiFi mid-service reconnects to the lanes it had, rather than
        # reverting to all four in front of the congregation.
        self.display_state = initial_state()
        # Operator sockets are tracked separately: the mic state carries a
        # level meter that updates several times a second, and every phone in
        # the congregation holds one of these sockets. Broadcasting it to all
        # of them would be pure waste, and would hand out the device labels of
        # the sound desk for no reason.
        self._operators = set()
        self.mic_state = initial_mic_state()

    async def register(self, ws, operator=False):
        snapshot = list(self._history)
        self._clients.add(ws)
        if operator:
            self._operators.add(ws)
        await ws.send_json({"type": "history", "sentences": snapshot})
        # After history, never before: a page applies the backlog and then
        # configures which lanes to render it into.
        await ws.send_json({"type": "display", **self.display_state})
        if operator:
            # The control room must not sit blank until the mic next speaks.
            await ws.send_json({"type": "mic", **self.mic_state})
        await self.publish_presence()

    def unregister(self, ws):
        self._clients.discard(ws)
        self._operators.discard(ws)

    async def broadcast(self, message):
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)
            logger.info("Dropped dead client (%d left)", len(self._clients))

    async def publish_sentence(self, sentence_id, en_text, final=False):
        """Publish (or revise) the English for a sentence.

        `final` is the correction contract the screens render: False is grey and
        may still change, True is solid and never will. Revision matters because
        the pipeline holds an unfinished sentence and re-publishes the same id
        with more words appended as the speaker keeps going.
        """
        for item in self._history:
            if item["id"] == sentence_id:
                item["en"] = en_text
                item["final"] = final
                break
        else:
            self._history.append(
                {"id": sentence_id, "en": en_text,
                 "translations": None, "final": final}
            )
        await self.broadcast(
            {"type": "sentence", "id": sentence_id, "en": en_text, "final": final})

    async def publish_translation(self, sentence_id, translations):
        for item in self._history:
            if item["id"] == sentence_id:
                item["translations"] = translations
                break
        await self.broadcast({"type": "translation", "id": sentence_id, **translations})

    async def clear_history(self):
        """Wipe the caption backlog from every screen in the building.

        Sends an empty `history`, which every page already treats as a full
        snapshot that replaces what it holds (see display.js and view.js) — so
        this needs no new client-side message type and no change to the wall.

        Does NOT touch transcripts/<date>.jsonl. That is the permanent record of
        what was said; this is only the live backlog a late-joining phone would
        be replayed. The two are deliberately different things.
        """
        self._history.clear()
        await self.broadcast({"type": "history", "sentences": []})

    async def publish_status(self, state):
        await self.broadcast({"type": "status", "state": state})

    async def broadcast_operators(self, message):
        """Like broadcast(), but only to sockets that proved the token."""
        dead = []
        for ws in list(self._operators):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)

    async def publish_presence(self):
        await self.broadcast_operators(
            {"type": "presence", "screens": len(self._clients)})

    async def publish_mic(self, state):
        """Validate, retain and send a complete capture state to operators.

        Retained for the same reason the wall state is: an operator opening the
        control room mid-service needs to see which microphone is live, not
        wait for the next level tick to find out.
        """
        self.mic_state = clean_mic_state(state)
        await self.broadcast_operators({"type": "mic", **self.mic_state})

    async def publish_mic_gone(self):
        """The capture page went away. Keep the last device list on screen —
        which mic it WAS using is exactly what you want to know when it drops —
        but stop claiming it is connected or still hearing anything."""
        self.mic_state = {**self.mic_state,
                          "connected": False, "capturing": False, "level": 0.0}
        await self.broadcast_operators({"type": "mic", **self.mic_state})

    async def publish_display(self, state):
        """Validate, retain and broadcast a complete wall configuration.

        Raises ValueError on a bad state, leaving the retained one untouched —
        a malformed control message must never blank the wall.
        """
        self.display_state = validate(state)
        await self.broadcast({"type": "display", **self.display_state})
