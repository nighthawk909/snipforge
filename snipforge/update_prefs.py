"""The user's update preferences, stored with the rest of SnipForge's settings.

Where updates come from is deliberately not a preference: only IT policy can point SnipForge at
another update location (snipforge/update_policy.py).
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from PySide6.QtCore import QSettings

from .settings import app_settings
from .updates import normalize_version

MODES = ("auto", "ask", "manual")       # install by itself / download then ask / only when asked
CHANNELS = ("stable", "preview")        # preview also gets prereleases
DEFAULT_MODE, DEFAULT_CHANNEL = "ask", "stable"

MODE_KEY, CHANNEL_KEY = "update_mode", "update_channel"
PIN_KEY, SKIP_KEY = "update_pinned_version", "skip_version"
LEGACY_AUTO_KEY = "auto_update"         # 0.5.x: True = check daily, False = never
LAST_CHECK_KEY = "last_update_check"
READY_KEY = "update_ready"              # the verified installer waiting to be installed
INSTALLED_KEY = "update_installed"      # what we just installed, to show what changed after restart


@dataclass(frozen=True)
class UpdatePrefs:
    mode: str = DEFAULT_MODE
    channel: str = DEFAULT_CHANNEL
    pinned_version: str | None = None   # never offer anything newer than this
    skip_version: str | None = None     # don't bring this version (or older) up again automatically

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"Update mode must be one of {MODES}, not {self.mode!r}")
        if self.channel not in CHANNELS:
            raise ValueError(f"Update channel must be one of {CHANNELS}, not {self.channel!r}")
        for name in ("pinned_version", "skip_version"):
            value = getattr(self, name)
            if value is None:
                continue
            normalized = normalize_version(str(value))
            if not normalized:
                raise ValueError(f"{name} must be a version like 0.6.0, not {value!r}")
            object.__setattr__(self, name, normalized)


def _text(settings: QSettings, key: str) -> str:
    value = settings.value(key, "")
    return value.strip() if isinstance(value, str) else ""


def _migrate(settings: QSettings) -> None:
    """0.5.x stored a single on/off switch; on means "ask", off means "manual"."""
    if not settings.contains(MODE_KEY) and settings.contains(LEGACY_AUTO_KEY):
        settings.setValue(MODE_KEY, "ask" if settings.value(LEGACY_AUTO_KEY, True, bool) else "manual")


def load_prefs(settings: QSettings | None = None) -> UpdatePrefs:
    """The saved preferences; anything missing or damaged falls back to the default."""
    settings = settings or app_settings()
    _migrate(settings)
    mode, channel = _text(settings, MODE_KEY).lower(), _text(settings, CHANNEL_KEY).lower()
    return UpdatePrefs(
        mode=mode if mode in MODES else DEFAULT_MODE,
        channel=channel if channel in CHANNELS else DEFAULT_CHANNEL,
        pinned_version=normalize_version(_text(settings, PIN_KEY)),
        skip_version=normalize_version(_text(settings, SKIP_KEY)),
    )


def save_prefs(prefs: UpdatePrefs, settings: QSettings | None = None) -> None:
    settings = settings or app_settings()
    settings.setValue(MODE_KEY, prefs.mode)
    settings.setValue(CHANNEL_KEY, prefs.channel)
    for key, value in ((PIN_KEY, prefs.pinned_version), (SKIP_KEY, prefs.skip_version)):
        if value:
            settings.setValue(key, value)
        else:
            settings.remove(key)
    # Older versions (after a rollback) only read the on/off switch: keep it in step, and off while
    # pinned so an older version doesn't offer the newest release straight away.
    settings.setValue(LEGACY_AUTO_KEY, prefs.mode != "manual" and not prefs.pinned_version)


def load_last_checked(settings: QSettings | None = None) -> dt.datetime | None:
    text = _text(settings or app_settings(), LAST_CHECK_KEY)
    try:
        return dt.datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def save_last_checked(when: dt.datetime, settings: QSettings | None = None) -> None:
    (settings or app_settings()).setValue(LAST_CHECK_KEY, when.isoformat(timespec="seconds"))


def load_record(key: str, settings: QSettings | None = None) -> dict | None:
    """A small JSON record (READY_KEY, INSTALLED_KEY); None if missing or damaged."""
    text = _text(settings or app_settings(), key)
    try:
        data = json.loads(text) if text else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def save_record(key: str, data: dict | None, settings: QSettings | None = None) -> None:
    settings = settings or app_settings()
    if data is None:
        settings.remove(key)
    else:
        settings.setValue(key, json.dumps(data))
