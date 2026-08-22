"""What the projector wall is currently showing.

One object describes the whole wall, so /control always sends a complete state
and never has to merge. Two operator phones therefore cannot drift apart: the
last complete message wins.

Pure functions, no I/O — the wall's rules are testable without a browser or a
server, which is where every interesting edge case lives.
"""
from config import (
    SOURCE_LANG, TARGET_LANGS, DEFAULT_LANES, ROTATE_MIN_S, ROTATE_MAX_S,
)

KNOWN_LANGS = (SOURCE_LANG, *TARGET_LANGS)


def initial_state():
    """The wall at startup. A fresh dict each call — callers mutate it."""
    return {"lanes": list(DEFAULT_LANES), "focus": None, "rotate": 0}


def _clean_lanes(raw):
    if not isinstance(raw, list) or not raw:
        raise ValueError("lanes must be a non-empty list")
    if any(lang not in KNOWN_LANGS for lang in raw):
        raise ValueError(f"lanes must be drawn from {KNOWN_LANGS}")
    if len(set(raw)) != len(raw):
        raise ValueError("lanes must not repeat")
    return list(raw)          # order is the wall order, so it is preserved


def _clean_rotate(raw):
    # bool is an int subclass and True would sail through the range check.
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError("rotate must be an integer number of seconds")
    if raw != 0 and not (ROTATE_MIN_S <= raw <= ROTATE_MAX_S):
        raise ValueError(f"rotate must be 0 or {ROTATE_MIN_S}-{ROTATE_MAX_S}")
    return raw


def validate(raw):
    """Clean an inbound state, or raise ValueError.

    Rejects unknown values rather than guessing, with one deliberate exception:
    a focus on a language that is no longer enabled is CLEARED rather than
    rejected. Disabling the focused lane is an ordinary operator action, and
    refusing it would leave the wall pinned to a language nobody selected.
    """
    if not isinstance(raw, dict):
        raise ValueError("state must be an object")

    lanes = _clean_lanes(raw.get("lanes", DEFAULT_LANES))
    rotate = _clean_rotate(raw.get("rotate", 0))

    focus = raw.get("focus")
    if focus is not None:
        if focus not in KNOWN_LANGS:
            raise ValueError(f"focus must be null or one of {KNOWN_LANGS}")
        if focus not in lanes:
            focus = None
    # Rotation and a pin are mutually exclusive; the display advances the
    # rotation locally, so a server-side focus would fight its timer.
    if rotate:
        focus = None

    return {"lanes": lanes, "focus": focus, "rotate": rotate}
