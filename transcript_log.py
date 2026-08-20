"""Append-only per-service transcript, one JSON object per line.

Written for two reasons: the service is worth keeping, and without a record
there is no way to tell whether a change to the STT or MT settings actually
improved anything. Failures here must never take the service down, so every
write is best-effort.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from config import TRANSCRIPT_DIR

logger = logging.getLogger(__name__)


class TranscriptLog:
    def __init__(self, directory=TRANSCRIPT_DIR, now=datetime.now):
        self.directory = Path(directory)
        self._now = now

    def _path(self, moment):
        return self.directory / f"{moment.date().isoformat()}.jsonl"

    def write(self, **record):
        moment = self._now()
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            line = json.dumps({"ts": moment.isoformat(), **record}, ensure_ascii=False)
            with self._path(moment).open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            logger.exception("Transcript write failed — continuing without it")
