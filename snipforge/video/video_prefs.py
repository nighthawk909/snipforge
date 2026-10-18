"""What recording remembers from one recording to the next: its sound sources, the pointer's effects
and the countdown. Kept with SnipForge's other settings, under video/; every read falls back to a
safe default when the stored value is missing or wrong."""
from __future__ import annotations

from dataclasses import dataclass, fields

from ..settings import app_settings

COUNTDOWNS = (0, 3, 5, 10)  # seconds before recording begins
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


def _read(name: str, default):
    value = app_settings().value(PREFIX + name, default)
    if isinstance(default, bool):
        if isinstance(value, str) and value.lower() in ("true", "false"):  # INI files store text
            return value.lower() == "true"
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return number if number in COUNTDOWNS else default
    return value if isinstance(value, str) and 0 < len(value) <= 512 else default


def load() -> RecordingChoices:
    defaults = RecordingChoices()
    return RecordingChoices(**{f.name: _read(f.name, getattr(defaults, f.name)) for f in fields(RecordingChoices)})


def save(choices: RecordingChoices) -> None:
    settings = app_settings()
    for f in fields(RecordingChoices):
        value = getattr(choices, f.name)
        if value is None:
            settings.remove(PREFIX + f.name)
        else:
            settings.setValue(PREFIX + f.name, value)
