import pytest
from hub import BroadcastHub


class FakeWS:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_json(self, msg):
        if self.fail:
            raise ConnectionError("gone")
        self.sent.append(msg)


async def test_register_replays_empty_history():
    hub = BroadcastHub()
    ws = FakeWS()
    await hub.register(ws)
    assert ws.sent == [{"type": "history", "sentences": []}]


async def test_sentence_then_translation_broadcast_to_all():
    hub = BroadcastHub()
    a, b = FakeWS(), FakeWS()
    await hub.register(a)
    await hub.register(b)
    await hub.publish_sentence(1, "God is love.")
    await hub.publish_translation(1, {"ml": "M", "te": "T", "hi": "H"})
    for ws in (a, b):
        assert {"type": "sentence", "id": 1, "en": "God is love."} in ws.sent
        assert {"type": "translation", "id": 1, "ml": "M", "te": "T", "hi": "H"} in ws.sent


async def test_late_joiner_gets_history_with_translations():
    hub = BroadcastHub()
    await hub.publish_sentence(1, "Hello.")
    await hub.publish_translation(1, {"ml": "M", "te": "T", "hi": "H"})
    late = FakeWS()
    await hub.register(late)
    history = late.sent[0]
    assert history["type"] == "history"
    assert history["sentences"] == [
        {"id": 1, "en": "Hello.", "translations": {"ml": "M", "te": "T", "hi": "H"}}
    ]


async def test_history_capped():
    hub = BroadcastHub(history_size=3)
    for i in range(5):
        await hub.publish_sentence(i, f"s{i}")
    late = FakeWS()
    await hub.register(late)
    ids = [s["id"] for s in late.sent[0]["sentences"]]
    assert ids == [2, 3, 4]


async def test_dead_client_is_dropped():
    hub = BroadcastHub()
    good, dead = FakeWS(), FakeWS(fail=True)
    await hub.register(good)
    hub._clients.add(dead)  # simulate a client whose socket died
    await hub.publish_status("listening")
    assert dead not in hub._clients
    assert {"type": "status", "state": "listening"} in good.sent


async def test_unregister_stops_delivery():
    hub = BroadcastHub()
    ws = FakeWS()
    await hub.register(ws)
    hub.unregister(ws)
    await hub.publish_status("listening")
    assert len(ws.sent) == 1  # only the history replay


async def test_sentence_published_during_registration_not_lost():
    hub = BroadcastHub()

    class SlowJoinWS(FakeWS):
        """Publishes a sentence mid-registration (while history is being sent)."""
        def __init__(self, hub):
            super().__init__()
            self.hub = hub
            self._first = True

        async def send_json(self, msg):
            if self._first:
                self._first = False
                await self.hub.publish_sentence(99, "mid-join sentence")
            await super().send_json(msg)

    ws = SlowJoinWS(hub)
    await hub.register(ws)
    # The sentence must reach the client somehow: live broadcast or history.
    got_live = any(m.get("type") == "sentence" and m.get("id") == 99 for m in ws.sent)
    in_history = any(
        s["id"] == 99
        for m in ws.sent if m.get("type") == "history"
        for s in m["sentences"]
    )
    assert got_live or in_history


async def test_republishing_a_sentence_updates_history_in_place():
    """Extending a held sentence must revise the row, not append a duplicate."""
    hub = BroadcastHub()
    await hub.publish_sentence(1, "But the translations")
    await hub.publish_sentence(1, "But the translations are good.")
    late = FakeWS()
    await hub.register(late)
    assert late.sent[0]["sentences"] == [
        {"id": 1, "en": "But the translations are good.", "translations": None}
    ]


async def test_republishing_preserves_existing_translations():
    hub = BroadcastHub()
    await hub.publish_sentence(1, "Hello.")
    await hub.publish_translation(1, {"ml": "M", "te": "T", "hi": "H"})
    await hub.publish_sentence(1, "Hello there.")
    late = FakeWS()
    await hub.register(late)
    row = late.sent[0]["sentences"][0]
    assert row["en"] == "Hello there."
    assert row["translations"] == {"ml": "M", "te": "T", "hi": "H"}
