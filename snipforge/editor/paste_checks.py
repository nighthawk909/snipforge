"""Checks on markup from the clipboard before it's pasted. Another app can put anything under
SnipForge's clipboard format, so a paste must never hang the editor, exhaust memory or crash it: these
limits are far beyond anything a real copy holds, and a paste over them is refused as a whole."""
from __future__ import annotations

import math

from PySide6.QtCore import QSizeF

from .stamps import STAMPS

MAX_NUMBER = 1_000_000.0  # any coordinate, size, width or layer
MAX_DEPTH = 6  # how deeply values nest in one item's state
MAX_TEXT = 5_000  # characters in one text box or callout
MAX_TOTAL_TEXT = 50_000
MAX_POINTS = 20_000  # in one pen or highlighter stroke
HEAVY_KINDS = {"blur", "pixelate", "erase", "magnify", "piece"}  # each keeps a picture of its area
MAX_PICTURES = 60_000_000  # characters of moved pieces' pictures (base64 PNG), together
MAX_HEAVY = 100
MAX_HEAVY_AREA = 4  # those areas together: at most this many times the snip's


def _numbers_ok(value, depth: int = 0) -> bool:
    if depth > MAX_DEPTH:
        return False
    if value is None or isinstance(value, (bool, str)):
        return True
    if isinstance(value, (int, float)):
        return (not isinstance(value, float) or math.isfinite(value)) and abs(value) <= MAX_NUMBER
    if isinstance(value, list):
        return all(_numbers_ok(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return all(_numbers_ok(v, depth + 1) for v in value.values())
    return False


def _area(state: dict, snip_area: float) -> float:
    size = state.get("size")
    if not (isinstance(size, list) and len(size) == 2 and all(isinstance(v, (int, float)) for v in size)):
        return 0.0
    return min(abs(size[0] * size[1]), snip_area)  # a picture is never bigger than the snip it shows


def problem(states: list[dict], snip: QSizeF) -> str | None:
    """Why these item states can't be pasted into a snip of this size, or None if they can."""
    snip_area = max(1.0, snip.width() * snip.height())
    total_text, heavy, heavy_area, pictures = 0, 0, 0.0, 0
    for state in states:
        if not _numbers_ok(state):
            return "it has numbers no snip uses"
        text = state.get("text", "")
        if not isinstance(text, str) or len(text) > MAX_TEXT:
            return "a text in it is too long"
        total_text += len(text)
        points = state.get("points", [])
        if not isinstance(points, list) or len(points) > MAX_POINTS:
            return "a drawing in it is too detailed"
        if state.get("kind") in HEAVY_KINDS:
            heavy += 1
            heavy_area += _area(state, snip_area)
        picture = state.get("image", "")
        pictures += len(picture) if isinstance(picture, str) else MAX_PICTURES + 1
    if total_text > MAX_TOTAL_TEXT:
        return "it has too much text"
    if heavy > MAX_HEAVY or heavy_area > MAX_HEAVY_AREA * snip_area:
        return "it has too many blurred, erased, moved or magnified areas"
    if pictures > MAX_PICTURES:
        return "its moved pieces are too big"
    return None


def tidy(state: dict) -> dict:
    """A copy of `state` that's safe to build: an unknown stamp becomes the check stamp here, rather
    than an entry in the stamp-artwork cache for every name another app makes up."""
    style = state.get("style")
    if state.get("kind") == "stamp" and isinstance(style, dict) and style.get("stamp") not in STAMPS:
        return {**state, "style": {**style, "stamp": "check"}}
    return state
