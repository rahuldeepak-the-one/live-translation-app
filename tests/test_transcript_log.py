"""Per-service transcript logging — nothing was persisted before this."""
import json
from datetime import datetime

from transcript_log import TranscriptLog


def fixed_now():
    return datetime(2026, 8, 18, 10, 30, 0)


def test_writes_one_json_line_per_utterance(tmp_path):
    log = TranscriptLog(tmp_path, now=fixed_now)
    log.write(id=1, en="God is love.", translations={"ml": "M", "te": "T", "hi": "H"})
    log.write(id=2, en="Amen.", translations={"ml": "A", "te": "A", "hi": "A"})

    lines = (tmp_path / "2026-08-18.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["en"] == "God is love."
    assert json.loads(lines[1])["translations"]["ml"] == "A"


def test_record_carries_a_timestamp(tmp_path):
    log = TranscriptLog(tmp_path, now=fixed_now)
    log.write(id=1, en="Hello.", translations={})
    record = json.loads((tmp_path / "2026-08-18.jsonl").read_text())
    assert record["ts"] == "2026-08-18T10:30:00"


def test_extra_fields_are_kept(tmp_path):
    log = TranscriptLog(tmp_path, now=fixed_now)
    log.write(id=1, en="Hi.", translations={}, stt_s=0.4, mt_s=1.1)
    record = json.loads((tmp_path / "2026-08-18.jsonl").read_text())
    assert record["stt_s"] == 0.4 and record["mt_s"] == 1.1


def test_creates_directory_if_missing(tmp_path):
    target = tmp_path / "nested" / "transcripts"
    TranscriptLog(target, now=fixed_now).write(id=1, en="Hi.", translations={})
    assert (target / "2026-08-18.jsonl").exists()


def test_unicode_is_written_readably(tmp_path):
    log = TranscriptLog(tmp_path, now=fixed_now)
    log.write(id=1, en="Thank you.", translations={"ml": "നന്ദി."})
    raw = (tmp_path / "2026-08-18.jsonl").read_text(encoding="utf-8")
    assert "നന്ദി." in raw  # not \uXXXX escapes


def test_default_directory_comes_from_config():
    from config import TRANSCRIPT_DIR
    assert str(TranscriptLog().directory) == TRANSCRIPT_DIR
