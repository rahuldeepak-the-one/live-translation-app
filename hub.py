"""Broadcast hub — pushes caption events to every connected screen."""
import logging
from collections import deque

from config import HISTORY_SIZE
from display_state import initial_state, validate

logger = logging.getLogger(__name__)


class BroadcastHub:
    def __init__(self, history_size=HISTORY_SIZE):
        self._clients = set()
        self._history = deque(maxlen=history_size)
        # Authoritative wall configuration. Retained so a screen that drops
        # WiFi mid-service reconnects to the lanes it had, rather than
        # reverting to all four in front of the congregation.
        self.display_state = initial_state()

    async def register(self, ws):
        snapshot = list(self._history)
        self._clients.add(ws)
        await ws.send_json({"type": "history", "sentences": snapshot})
        # After history, never before: a page applies the backlog and then
        # configures which lanes to render it into.
        await ws.send_json({"type": "display", **self.display_state})

    def unregister(self, ws):
        self._clients.discard(ws)

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

    async def publish_status(self, state):
        await self.broadcast({"type": "status", "state": state})

    async def publish_display(self, state):
        """Validate, retain and broadcast a complete wall configuration.

        Raises ValueError on a bad state, leaving the retained one untouched —
        a malformed control message must never blank the wall.
        """
        self.display_state = validate(state)
        await self.broadcast({"type": "display", **self.display_state})
