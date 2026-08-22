import pytest

from display_state import KNOWN_LANGS, initial_state, validate


def test_initial_state_shows_every_language():
    assert initial_state() == {
        "lanes": ["en", "ml", "te", "hi"], "focus": None, "rotate": 0}


def test_initial_state_is_not_shared_between_calls():
    first = initial_state()
    first["lanes"].append("xx")
    assert "xx" not in initial_state()["lanes"]


def test_known_langs_comes_from_config():
    assert KNOWN_LANGS == ("en", "ml", "te", "hi")


def test_valid_state_passes_through_with_lane_order_preserved():
    raw = {"lanes": ["hi", "ml"], "focus": "ml", "rotate": 0}
    assert validate(raw) == {"lanes": ["hi", "ml"], "focus": "ml", "rotate": 0}


def test_missing_keys_fall_back_to_defaults():
    assert validate({"lanes": ["ml"]}) == {
        "lanes": ["ml"], "focus": None, "rotate": 0}


@pytest.mark.parametrize("lanes", [[], ["xx"], ["ml", "ml"], "ml", None])
def test_bad_lanes_rejected(lanes):
    with pytest.raises(ValueError):
        validate({"lanes": lanes})


@pytest.mark.parametrize("rotate", [-1, 4, 121, "20", 1.5])
def test_bad_rotate_rejected(rotate):
    with pytest.raises(ValueError):
        validate({"lanes": ["ml"], "rotate": rotate})


@pytest.mark.parametrize("rotate", [0, 5, 20, 120])
def test_rotate_bounds_accepted(rotate):
    assert validate({"lanes": ["ml", "te"], "rotate": rotate})["rotate"] == rotate


@pytest.mark.parametrize("rotate", [True, False])
def test_booleans_are_not_valid_rotate_values(rotate):
    # bool subclasses int, so False (== 0) would otherwise pass as "disabled"
    # and True (== 1) is only caught by the range check by accident. The guard
    # in _clean_rotate exists for this; without these cases nothing proves it.
    with pytest.raises(ValueError):
        validate({"lanes": ["ml"], "rotate": rotate})


def test_focus_on_a_disabled_language_is_cleared_not_rejected():
    # Repairing rather than raising: the operator disabling the focused lane is
    # an ordinary action, and blanking the wall over it would be worse than
    # falling back to showing everything.
    assert validate({"lanes": ["ml", "te"], "focus": "hi"})["focus"] is None


def test_unknown_focus_is_rejected():
    with pytest.raises(ValueError):
        validate({"lanes": ["ml"], "focus": "xx"})


def test_rotation_clears_any_pin():
    # The display owns the rotation position; a server-side focus would fight it.
    assert validate({"lanes": ["ml", "te"], "focus": "ml", "rotate": 20})["focus"] is None
