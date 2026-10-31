"""IT policy for updates: HKLM, then HKCU, under Software\\Policies\\SnipForge.

| Value              | Type      | Effect                                                            |
|--------------------|-----------|-------------------------------------------------------------------|
| DisableUpdateCheck | DWORD 1   | No update checks or downloads at all                              |
| UpdateSource       | "github" / "feed" | Where releases come from (setting UpdateFeed implies "feed") |
| UpdateFeed         | string    | Folder (UNC or synced SharePoint library) or https:// URL holding releases.json |
| UpdateMode         | string    | Forces "auto", "ask" or "manual"                                  |
| UpdateChannel      | string    | Forces "stable" or "preview"                                      |
| AllowRollback      | DWORD 0   | Blocks installing older versions                                  |

Machine values win over user values; for the two switches the more restrictive one wins, and the
source (UpdateSource/UpdateFeed) comes from the highest hive that sets either. Only policy can set a
feed location, and HKCU\\Software\\Policies is writable only by administrators.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import PureWindowsPath
from urllib.parse import urlparse

from . import update_platform
from .update_prefs import CHANNELS, MODES, UpdatePrefs

SOURCES = ("github", "feed")
MANAGED_MESSAGE = "Updates are managed by your organization."


@dataclass(frozen=True)
class PolicyInfo:
    disabled: bool = False              # DisableUpdateCheck
    source: str = "github"              # "github" or "feed"
    feed: str | None = None             # validated feed location when source is "feed"
    mode: str | None = None             # forced mode, or None if the user chooses
    channel: str | None = None          # forced channel, or None if the user chooses
    allow_rollback: bool = True
    problems: tuple[str, ...] = ()      # policy values that were ignored, for "Managed by your organization"

    @property
    def managed(self) -> bool:
        """True when any policy affects updates, so the UI can say "Managed by your organization"."""
        return (self.disabled or self.source != "github" or self.mode is not None or self.channel is not None
                or not self.allow_rollback or bool(self.problems))

    @property
    def source_problem(self) -> str:
        """Why the configured feed can't be used ("" when it can, or when GitHub is the source)."""
        if self.source != "feed" or self.feed:
            return ""
        feed_problems = [p for p in self.problems if "UpdateFeed" in p]
        return feed_problems[0] if feed_problems else "Your organization's update location (UpdateFeed) isn't set."

    def apply(self, prefs: UpdatePrefs) -> UpdatePrefs:
        """The preferences in effect: forced values replace the user's."""
        return replace(prefs, mode=self.mode or prefs.mode, channel=self.channel or prefs.channel)


def feed_location(text) -> str | None:
    """A usable feed location (https URL or absolute folder/file path), or None."""
    if not isinstance(text, str):
        return None
    text = os.path.expandvars(text.strip().strip('"').strip())
    if not text:
        return None
    if "://" in text or text.lower().startswith(("http:", "https:", "file:", "ftp:")):
        parsed = urlparse(text)
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
            return None
        return text
    path = PureWindowsPath(text)
    unexpanded = re.search(r"%[^%\\/]+%", text)  # a variable that doesn't exist on this PC
    return str(path) if path.is_absolute() and not unexpanded else None


def _flag(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str) and value.strip().lower() in ("0", "1", "true", "false"):
        return value.strip().lower() in ("1", "true")
    return None


def _first(hives: Sequence[Mapping[str, object]], name: str):
    for hive in hives:
        if name in hive and hive[name] not in (None, ""):
            return hive[name]
    return None


def _choice(value, allowed: tuple[str, ...], name: str, problems: list[str]) -> str | None:
    if value is None:
        return None
    text = value.strip().lower() if isinstance(value, str) else None
    if text in allowed:
        return text
    problems.append(f"{name} must be one of {', '.join(allowed)}; ignoring {value!r}.")
    return None


def parse_policy(hives: Sequence[Mapping[str, object]]) -> PolicyInfo:
    """Turn raw policy values (highest precedence first) into a PolicyInfo."""
    problems: list[str] = []
    disabled = any(_flag(h.get("DisableUpdateCheck")) is True for h in hives)
    allow_rollback = not any(_flag(h.get("AllowRollback")) is False for h in hives)
    mode = _choice(_first(hives, "UpdateMode"), MODES, "UpdateMode", problems)
    channel = _choice(_first(hives, "UpdateChannel"), CHANNELS, "UpdateChannel", problems)
    # Where updates come from is decided by the highest hive that says anything about it, so a user
    # value can't turn a machine-wide feed back into GitHub.
    deciding = next((h for h in hives if _first([h], "UpdateSource") is not None
                     or _first([h], "UpdateFeed") is not None), {})
    source = _choice(_first([deciding], "UpdateSource"), SOURCES, "UpdateSource", problems)
    raw_feed = _first(hives, "UpdateFeed")
    if source is None:
        source = "feed" if _first([deciding], "UpdateFeed") is not None else "github"
    feed = None
    if source == "feed":
        feed = feed_location(raw_feed)
        if raw_feed is None:
            problems.append("UpdateSource is set to feed, but UpdateFeed isn't set.")
        elif feed is None:
            problems.append("UpdateFeed must be an https:// address or a full folder path; "
                            f"ignoring {raw_feed!r}.")
    return PolicyInfo(disabled, source, feed, mode, channel, allow_rollback, tuple(problems))


def read_policy() -> PolicyInfo:
    return parse_policy(update_platform.read_policy_hives())


def disabled_by_policy() -> bool:
    return read_policy().disabled
