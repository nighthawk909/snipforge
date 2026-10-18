"""Telling private browsing windows apart, so a snip of one never keeps its window title (the
Library, its search and the "App name and date" stamp show only the app).

Titles are compared folded: lower case, accents and zero-width characters removed, and odd spaces
(no-break, ideographic) made plain. A few markers are unmistakable in any app ("InPrivate",
"Incognito", "Private browsing"); the many-language hints ("privat", "inkognit", "无痕"...) are
only trusted in a browser, where they mean a private window rather than a file about privacy.
"""
from __future__ import annotations

import unicodedata

CLEAR_MARKERS = ("inprivate", "incognito", "private browsing")
# Folded words that mark a private window in some browser or language.
HINTS = (
    *CLEAR_MARKERS, "privat", "privad", "prive", "incognit", "inkognit", "gizli",
    "シークレット",  # Japanese: secret (Chrome's incognito)
    "无痕", "隐私", "隱私",  # Chinese: traceless, private
    "시크릿",  # Korean: secret
    "приват", "инкогнито",  # Russian
)
# Words in an app's name that make it a browser ("Microsoft Edge", "Brave Browser", ...).
BROWSER_WORDS = {"edge", "chrome", "chromium", "firefox", "brave", "opera", "vivaldi", "browser", "safari",
                 "arc", "yandex", "waterfox", "librewolf", "floorp", "thorium", "whale", "duckduckgo", "tor"}


def folded(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if unicodedata.category(c) not in ("Mn", "Cf"))  # accents, zero-widths
    return " ".join(plain.casefold().split())


def is_browser(app: str) -> bool:
    words = "".join(c if c.isalnum() else " " for c in folded(app)).split()
    return any(word in BROWSER_WORDS for word in words)


def is_private_title(title: str) -> bool:
    """Whether a title might be a private window's, in any app. Where showing too little is the
    safe mistake (the stamp), this is the check to use."""
    text = folded(title)
    return any(hint in text for hint in HINTS)


def is_private_window(title: str, app: str) -> bool:
    """Whether a window is a private browsing window: the check for what's saved with a snip."""
    text = folded(title)
    if any(marker in text for marker in CLEAR_MARKERS):
        return True
    return is_browser(app) and any(hint in text for hint in HINTS)
