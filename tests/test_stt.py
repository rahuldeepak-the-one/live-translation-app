import numpy as np
import pytest

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def stt():
    from stt import WhisperSTT
    return WhisperSTT()


async def test_silence_transcribes_to_empty(stt):
    silence = np.zeros(2 * 16000, dtype=np.int16)
    text = await stt.transcribe(silence)
    assert text == ""
