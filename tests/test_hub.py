import pytest
from hub import BroadcastHub
from display_state import initial_state


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
    history = next(m for m in ws.sent if m["type"] == "history")
    assert history == {"type": "history", "sentences": []}


async def test_sentence_then_translation_broadcast_to_all():
    hub = BroadcastHub()
    a, b = FakeWS(), FakeWS()
    await hub.register(a)
    await hub.register(b)
    await hub.publish_sentence(1, "God is love.")
    await hub.publish_translation(1, {"ml": "M", "te": "T", "hi": "H"})
    for ws in (a, b):
        assert {"type": "sentence", "id": 1, "en": "God is love.", "final": False} in ws.sent
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
        {"id": 1, "en": "Hello.", "translations": {"ml": "M", "te": "T", "hi": "H"},
         "final": False}
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
    assert len(ws.sent) == 2  # only the history + display replay from register()


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
        {"id": 1, "en": "But the translations are good.", "translations": None,
         "final": False}
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


async def test_register_replays_display_state_after_history():
    hub = BroadcastHub()
    ws = FakeWS()
    await hub.register(ws)
    # Order matters: a page applies history first, then configures its lanes.
    assert [m["type"] for m in ws.sent] == ["history", "display"]
    assert ws.sent[1] == {"type": "display", **initial_state()}


async def test_publish_display_is_retained_for_late_joiners():
    hub = BroadcastHub()
    await hub.publish_display({"lanes": ["ml", "te"], "focus": "ml", "rotate": 0})
    late = FakeWS()
    await hub.register(late)
    assert late.sent[1] == {
        "type": "display", "lanes": ["ml", "te"], "focus": "ml", "rotate": 0}


async def test_publish_display_broadcasts_to_connected_screens():
    hub = BroadcastHub()
    ws = FakeWS()
    await hub.register(ws)
    ws.sent.clear()
    await hub.publish_display({"lanes": ["hi"], "focus": None, "rotate": 0})
    assert ws.sent == [
        {"type": "display", "lanes": ["hi"], "focus": None, "rotate": 0}]


async def test_publish_display_rejects_bad_state_and_keeps_the_old_one():
    hub = BroadcastHub()
    await hub.publish_display({"lanes": ["ml"], "focus": None, "rotate": 0})
    with pytest.raises(ValueError):
        await hub.publish_display({"lanes": []})
    assert hub.display_state["lanes"] == ["ml"]


async def test_sentence_carries_final_flag():
    hub = BroadcastHub()
    ws = FakeWS()
    await hub.register(ws)
    await hub.publish_sentence(1, "Half a sen", final=False)
    await hub.publish_sentence(1, "Half a sentence finished.", final=True)
    sentences = [m for m in ws.sent if m["type"] == "sentence"]
    assert sentences == [
        {"type": "sentence", "id": 1, "en": "Half a sen", "final": False},
        {"type": "sentence", "id": 1, "en": "Half a sentence finished.", "final": True},
    ]


async def test_history_retains_final():
    hub = BroadcastHub()
    await hub.publish_sentence(1, "Done.", final=True)
    late = FakeWS()
    await hub.register(late)
    assert late.sent[0]["sentences"] == [
        {"id": 1, "en": "Done.", "translations": None, "final": True}]


async def test_revising_a_sentence_updates_final_in_history():
    hub = BroadcastHub()
    await hub.publish_sentence(1, "Growing", final=False)
    await hub.publish_sentence(1, "Growing still.", final=True)
    late = FakeWS()
    await hub.register(late)
    assert late.sent[0]["sentences"][0]["final"] is True


# --------------------------------------------------------------- mic state
# The capture device lives in the mic PAGE, so the hub's job is only to retain
# what that page reports and show it to operators.

async def test_mic_state_reaches_operators_only():
    hub = BroadcastHub()
    operator, screen = FakeWS(), FakeWS()
    await hub.register(operator, operator=True)
    await hub.register(screen)
    await hub.publish_mic({"capturing": True, "deviceId": "usb-1",
                           "deviceLabel": "Yamaha MG10XU", "level": 0.4,
                           "devices": [{"id": "usb-1", "label": "Yamaha MG10XU"}]})
    assert any(m["type"] == "mic" for m in operator.sent)
    # A congregation phone holds one of these sockets too. The level ticks
    # several times a second and the labels name the sound desk; neither has
    # any business going to every seat in the building.
    assert not any(m["type"] == "mic" for m in screen.sent)


async def test_operator_joining_late_sees_the_current_mic():
    hub = BroadcastHub()
    await hub.publish_mic({"capturing": True, "deviceId": "usb-1",
                           "deviceLabel": "Yamaha", "devices": []})
    late = FakeWS()
    await hub.register(late, operator=True)
    mic = next(m for m in late.sent if m["type"] == "mic")
    assert mic["deviceLabel"] == "Yamaha" and mic["capturing"] is True


async def test_mic_state_is_clamped_not_trusted():
    # /ws/mic carries no token, so this state is unvetted input.
    hub = BroadcastHub()
    ws = FakeWS()
    await hub.register(ws, operator=True)
    await hub.publish_mic({
        "capturing": "yes", "level": 99, "deviceId": 12345,
        "devices": [{"id": "a", "label": "Real"}, "junk", {"id": 1, "label": 2}]
                   + [{"id": str(i), "label": str(i)} for i in range(50)],
    })
    mic = [m for m in ws.sent if m["type"] == "mic"][-1]
    assert mic["level"] == 1.0                 # clamped into range
    assert mic["deviceId"] is None             # a non-string is dropped, not cast
    assert all(isinstance(d["id"], str) for d in mic["devices"])
    assert len(mic["devices"]) <= 24


async def test_a_true_level_is_not_read_as_full_scale():
    # bool is an int subclass; an unguarded isinstance check clamps True to 1.0
    # and paints a muted microphone as peaking.
    hub = BroadcastHub()
    ws = FakeWS()
    await hub.register(ws, operator=True)
    await hub.publish_mic({"level": True, "devices": []})
    assert [m for m in ws.sent if m["type"] == "mic"][-1]["level"] == 0.0


async def test_losing_the_mic_page_keeps_the_last_device_on_screen():
    hub = BroadcastHub()
    ws = FakeWS()
    await hub.register(ws, operator=True)
    await hub.publish_mic({"capturing": True, "deviceId": "usb-1",
                           "deviceLabel": "Yamaha", "devices": [],  "level": 0.5})
    await hub.publish_mic_gone()
    mic = [m for m in ws.sent if m["type"] == "mic"][-1]
    # Which mic it WAS on is exactly what you want to read when it drops.
    assert mic["deviceLabel"] == "Yamaha"
    assert mic["connected"] is False and mic["capturing"] is False
    assert mic["level"] == 0.0


async def test_presence_counts_every_screen_for_the_operator():
    hub = BroadcastHub()
    operator = FakeWS()
    await hub.register(operator, operator=True)
    await hub.register(FakeWS())
    await hub.register(FakeWS())
    assert [m for m in operator.sent if m["type"] == "presence"][-1]["screens"] == 3
    hub.unregister(operator)
    assert operator not in hub._operators


# ------------------------------------------------------------ clearing the wall

async def test_clearing_sends_every_screen_an_empty_snapshot():
    hub = BroadcastHub()
    screen = FakeWS()
    await hub.publish_sentence(1, "Turn with me to Matthew.")
    await hub.register(screen)
    await hub.clear_history()
    # Not a new message type on purpose: every page already treats `history` as
    # a full snapshot, so this clears the wall without touching display.js.
    assert screen.sent[-1] == {"type": "history", "sentences": []}


async def test_a_screen_joining_after_a_clear_gets_nothing_back():
    hub = BroadcastHub()
    await hub.publish_sentence(1, "Turn with me to Matthew.")
    await hub.clear_history()
    late = FakeWS()
    await hub.register(late)
    assert next(m for m in late.sent if m["type"] == "history")["sentences"] == []


async def test_clearing_reaches_read_only_screens_too():
    # The backlog lives on every phone in the building, not just operators'.
    hub = BroadcastHub()
    operator, congregant = FakeWS(), FakeWS()
    await hub.register(operator, operator=True)
    await hub.register(congregant)
    await hub.clear_history()
    for ws in (operator, congregant):
        assert ws.sent[-1] == {"type": "history", "sentences": []}
