from hub import BroadcastHub
from pipeline import UtterancePipeline
from tests.test_hub import FakeWS


class StubSTT:
    def __init__(self, text):
        self.text = text

    async def transcribe(self, audio_np):
        return self.text


class StubTranslator:
    async def translate_all(self, text):
        return {"ml": f"ml:{text}", "te": f"te:{text}", "hi": f"hi:{text}"}


async def test_speech_flows_to_screens():
    hub = BroadcastHub()
    screen = FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(StubSTT("Hello world."), StubTranslator(), hub)

    result = await pipe.process(None)  # stub ignores audio

    assert result == (1, "Hello world.")
    types = [m["type"] for m in screen.sent]
    assert types == ["history", "sentence", "translation"]
    assert screen.sent[2]["ml"] == "ml:Hello world."


async def test_ids_increment():
    hub = BroadcastHub()
    pipe = UtterancePipeline(StubSTT("Hi."), StubTranslator(), hub)
    assert (await pipe.process(None))[0] == 1
    assert (await pipe.process(None))[0] == 2


async def test_empty_transcription_publishes_nothing():
    hub = BroadcastHub()
    screen = FakeWS()
    await hub.register(screen)
    pipe = UtterancePipeline(StubSTT(""), StubTranslator(), hub)

    assert await pipe.process(None) is None
    assert [m["type"] for m in screen.sent] == ["history"]
