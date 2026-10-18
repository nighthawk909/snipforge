"""What recording remembers from one recording to the next: its sound sources, the pointer's effects,
the countdown, and the webcam (on or off, which camera, and its bubble's shape, size and place). Kept
with SnipForge's other settings, under video/; every read falls back to a safe default when the
stored value is missing or wrong."""
from __future__ import annotations

from dataclasses import dataclass, fields

from ..settings import app_settings

COUNTDOWNS = (0, 3, 5, 10)  # seconds before recording begins
BUBBLE_SHAPES = ("circle", "rounded", "rectangle")  # webcam_layer.SHAPES (not imported: loads Qt Gui)
BUBBLE_SIZES = ("small", "medium", "large")  # webcam_bubble's sizes
PREFIX = "video/"


@dataclass(frozen=True)
class RecordingChoices:
    microphone: bool = True
    microphone_id: str | None = None  # None: Windows' default microphone
    system_audio: bool = True
    pointer: bool = True
    highlight: bool = True
    clicks: bool = True
    countdown: int = 3
    webcam: bool = False
    webcam_id: str | None = None  # None: Windows' default camera
    bubble_shape: str = "circle"
    bubble_size: str = "medium"
    bubble_x: int | None = None  # where the bubble was left, in physical pixels; None: by the area
    bubble_y: int | None = None


def _flag(value, default: bool) -> bool:
    if isinstance(value, str) and value.lower() in ("true", "false"):  # INI files store text
        return value.lower() == "true"
    return value if isinstance(value, bool) else default


def _whole(value, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if -100_000 < number < 100_000 else default


def _text(value, default):
    return value if isinstance(value, str) and 0 < len(value) <= 512 else default


def _one_of(choices):
    return lambda value, default: value if value in choices else default


CHECKS = {
    "countdown": lambda value, default: (n if (n := _whole(value, default)) in COUNTDOWNS else default),
    "bubble_shape": _one_of(BUBBLE_SHAPES),
    "bubble_size": _one_of(BUBBLE_SIZES),
    "bubble_x": _whole,
    "bubble_y": _whole,
    "microphone_id": _text,
    "webcam_id": _text,
}


def load() -> RecordingChoices:
    defaults, settings, values = RecordingChoices(), app_settings(), {}
    for f in fields(RecordingChoices):
        default = getattr(defaults, f.name)
        stored = settings.value(PREFIX + f.name, default)
        check = CHECKS.get(f.name, _flag)
        values[f.name] = check(stored, default)
    return RecordingChoices(**values)


def save(choices: RecordingChoices) -> None:
    settings = app_settings()
    for f in fields(RecordingChoices):
        value = getattr(choices, f.name)
        if value is None:
            settings.remove(PREFIX + f.name)
        else:
            settings.setValue(PREFIX + f.name, value)
