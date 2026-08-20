"""Broadcast hub — pushes caption events to every connected screen."""
import logging
from collections import deque

from config import HISTORY_SIZE

logger = logging.getLogger(__name__)


class BroadcastHub:
    def __init__(self, history_size=HISTORY_SIZE):
        self._clients = set()
        self._history = deque(maxlen=history_size)

    async def register(self, ws):
        snapshot = list(self._history)
        self._clients.add(ws)
        await ws.send_json({"type": "history", "sentences": snapshot})

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

    async def publish_sentence(self, sentence_id, en_text):
        """Publish (or revise) the English for a sentence.

        Revision matters because the pipeline holds an unfinished sentence and
        re-publishes the same id with more words appended as the speaker keeps
        going. Updating the row in place — rather than appending — keeps history
        matching what the screens show, which apply `sentence` by id too.
        """
        for item in self._history:
            if item["id"] == sentence_id:
                item["en"] = en_text
                break
        else:
            self._history.append(
                {"id": sentence_id, "en": en_text, "translations": None}
            )
        await self.broadcast({"type": "sentence", "id": sentence_id, "en": en_text})

    async def publish_translation(self, sentence_id, translations):
        for item in self._history:
            if item["id"] == sentence_id:
                item["translations"] = translations
                break
        await self.broadcast({"type": "translation", "id": sentence_id, **translations})

    async def publish_status(self, state):
        await self.broadcast({"type": "status", "state": state})
