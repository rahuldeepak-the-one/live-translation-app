import pytest

from transcript_log import TranscriptLog


@pytest.fixture(autouse=True)
def isolate_transcripts(tmp_path, monkeypatch):
    """Stop tests appending stub utterances to the project's real transcripts/.

    create_app() builds a default TranscriptLog for real deployments, so any
    test that exercises the mic socket would otherwise write there.
    """
    import server
    monkeypatch.setattr(
        server, "TranscriptLog",
        lambda *args, **kwargs: TranscriptLog(tmp_path / "transcripts"),
    )
