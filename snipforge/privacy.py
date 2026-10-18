"""Telling private browsing windows apart, so a snip of one doesn't keep its window title (the
Library, its search and the "App name and date" stamp show only the app). It's a best-effort check
on the window's title and app name.

Titles are compared folded: lower case, accents and zero-width characters removed, odd spaces
(no-break, ideographic) made plain. A few markers are unmistakable anywhere in any app's title
("InPrivate", "Incognito", "Private browsing"). The many-language hints ("privat", "inkognit",
"无痕"...) count only in a browser, and only where browsers put their private marker: the last
part of the title (after the page's own " - " or " — ") or in brackets, so a page called
"Private equity - Wikipedia" keeps its title.
"""
from __future__ import annotations

import re
import unicodedata

CHECK_VERSION = 2  # bump when the hints change: titles saved before are checked again (Library)


def folded(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if unicodedata.category(c) not in ("Mn", "Cf"))  # accents, zero-widths
    return " ".join(plain.casefold().split())


CLEAR_MARKERS = tuple(folded(m) for m in ("inprivate", "incognito", "private browsing"))
# Words that mark a private window in some browser or language. Folded like the titles they're
# looked for in (folding splits Korean syllables and drops Japanese voicing marks, for example).
HINTS = tuple(folded(h) for h in (
    *CLEAR_MARKERS, "privat", "privad", "prive", "prywat", "incognit", "inkognit", "anonim", "anonym",
    "yksityi", "pribadi", "an danh", "gizli",
    "シークレット", "プライベート",  # Japanese: Chrome's secret mode, Firefox's private browsing
    "无痕", "無痕", "隐私", "隱私",  # Chinese: traceless, private
    "시크릿",  # Korean: secret
    "приват", "инкогнито",  # Russian
    "متخفي",  # Arabic: hidden
    "בסתר",  # Hebrew: in secret
    "ανωνυμ", "ιδιωτικ",  # Greek: anonymous, private
))
ALWAYS_PRIVATE = ("tor browser", "mullvad browser")  # every window of these is private
# Words in an app's name, or in a title's last part, that make it a browser ("Microsoft Edge",
# "Brave Browser", "Mozilla Firefox"...).
BROWSER_WORDS = {"edge", "chrome", "chromium", "firefox", "brave", "opera", "vivaldi", "safari", "arc", "yandex",
                 "waterfox", "librewolf", "floorp", "thorium", "whale", "duckduckgo", "seamonkey", "maxthon",
                 "palemoon", "mullvad", "tor"}
BROWSER_PHRASES = ("internet explorer", "pale moon", "zen browser", "浏览器", "瀏覽器")
_SEPARATOR = re.compile(r"\s[-–—]\s")  # " - ", " – ", " — " between a page and its browser
_BRACKETED = re.compile(r"[(\[]([^()\[\]]{1,40})[)\]]")


def is_browser(name: str) -> bool:
    text = folded(name)
    words = "".join(c if c.isalnum() else " " for c in text).split()
    return any(word in BROWSER_WORDS for word in words) or any(phrase in text for phrase in BROWSER_PHRASES)


def is_private_title(title: str) -> bool:
    """Whether a title might be a private window's, anywhere in it and in any app. The stamp uses
    this: a stamp ends up in an image people share, so there saying too little is the safe mistake."""
    text = folded(title)
    return any(hint in text for hint in HINTS)


def is_private_window(title: str, app: str) -> bool:
    """Whether a window is a private browsing window: the check for what's kept with a snip."""
    text = folded(title)
    if any(marker in text for marker in CLEAR_MARKERS):
        return True
    tail = _SEPARATOR.split(text)[-1]
    if not (is_browser(app) or is_browser(tail)):
        return False
    if any(name in tail for name in ALWAYS_PRIVATE) or any(name in folded(app) for name in ALWAYS_PRIVATE):
        return True
    places = (tail, *_BRACKETED.findall(text))
    return any(hint in place for place in places for hint in HINTS)
