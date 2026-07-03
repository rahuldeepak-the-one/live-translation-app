"""One utterance through the system: audio -> English -> 3 translations -> screens."""
import logging
import time

logger = logging.getLogger(__name__)


class UtterancePipeline:
    def __init__(self, stt, translator, hub):
        self.stt = stt
        self.translator = translator
        self.hub = hub
        self._counter = 0

    async def process(self, audio_np):
        t0 = time.time()
        text = await self.stt.transcribe(audio_np)
        if not text:
            return None
        t_stt = time.time() - t0

        self._counter += 1
        sid = self._counter
        await self.hub.publish_sentence(sid, text)

        t1 = time.time()
        translations = await self.translator.translate_all(text)
        await self.hub.publish_translation(sid, translations)
        logger.info(
            "#%d stt=%.2fs mt=%.2fs: %s", sid, t_stt, time.time() - t1, text
        )
        return sid, text
