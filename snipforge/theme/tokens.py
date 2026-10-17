"""Design tokens: every color a SnipForge window is drawn with.

A Theme is resolved from three inputs: light or dark, an accent, and an optional header band
(organization theme packs use one). Everything here is immutable; changing a preference
resolves a new Theme.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

HEX = re.compile(r"#[0-9A-Fa-f]{6}")  # use with fullmatch
MODES = ("light", "dark")
DEFAULT_ACCENT = "snipforge"
BRAND = "#2F5BEA"  # the app icon; not themed


def _channels(hex_color: str) -> tuple[int, int, int]:
    if not isinstance(hex_color, str) or not HEX.fullmatch(hex_color):
        raise ValueError(f"not a #RRGGBB color: {hex_color!r}")
    return int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)


def to_qcolor(value: str):
    """'#RRGGBB' or the 'rgba(r, g, b, a)' strings this module makes -> QColor (for painted widgets)."""
    from PySide6.QtGui import QColor

    if value.startswith("rgba("):
        r, g, b, a = (int(part) for part in value[5:-1].split(","))
        return QColor(r, g, b, a)
    return QColor(value)


def rgba(hex_color: str, alpha: float) -> str:
    """'#RRGGBB' and a 0..1 alpha -> 'rgba(r, g, b, a)' with the 0..255 alpha Qt stylesheets expect."""
    r, g, b = _channels(hex_color)
    return f"rgba({r}, {g}, {b}, {round(max(0.0, min(1.0, alpha)) * 255)})"


def _luminance(hex_color: str) -> float:
    def linear(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in _channels(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 2 contrast ratio between two #RRGGBB colors (1 to 21)."""
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@dataclass(frozen=True)
class Palette:
    bg: str          # window background
    surface: str     # panels, cards, inputs
    surface2: str    # buttons, raised controls
    well: str        # behind the snip in the editor
    dot: str         # dot grid in the well
    border: str
    text: str
    text2: str       # secondary text and icons
    text3: str       # captions, hints
    hover: str
    select: str      # selected list rows and nav items
    danger: str
    success: str


LIGHT = Palette(
    bg="#F3F3F5", surface="#FFFFFF", surface2="#F6F6F8", well="#E7E7EC", dot="#CFCFD8", border="#E1E1E7",
    text="#1B1B1F", text2="#45454F", text3="#676772", hover="#EAEAEF", select="#E4E4EA",
    danger="#C42B1C", success="#0F7B0F")
DARK = Palette(
    bg="#1B1B1E", surface="#232327", surface2="#2B2B30", well="#141416", dot="#2C2C33", border="#36363D",
    text="#F2F2F4", text2="#C4C4CC", text3="#9C9CA6", hover="#303036", select="#34343B",
    danger="#FF99A4", success="#6CCB5F")
PALETTES = {"light": LIGHT, "dark": DARK}


@dataclass(frozen=True)
class AccentShade:
    fill: str        # primary buttons, checked controls
    fill_hover: str
    on: str          # text on fill
    text: str        # accent-colored text and icons on surfaces
    soft: str        # selected tool, chip and tag backgrounds


@dataclass(frozen=True)
class Accent:
    id: str
    name: str
    light: AccentShade
    dark: AccentShade

    def shade(self, mode: str) -> AccentShade:
        return self.dark if mode == "dark" else self.light


BUILTIN_ACCENTS: dict[str, Accent] = {
    "snipforge": Accent(
        "snipforge", "SnipForge blue",
        AccentShade(fill="#2F5BEA", fill_hover="#2549C8", on="#FFFFFF", text="#2549C8", soft="#E8EEFF"),
        AccentShade(fill="#3D66F0", fill_hover="#3358E0", on="#FFFFFF", text="#93ACFF", soft="#26305C")),
    "teal": Accent(
        "teal", "Teal",
        AccentShade(fill="#0E7C86", fill_hover="#0B6670", on="#FFFFFF", text="#0B6670", soft="#E2F4F5"),
        AccentShade(fill="#13808B", fill_hover="#0F6F79", on="#FFFFFF", text="#5FD0DB", soft="#173B3F")),
    "graphite": Accent(
        "graphite", "Graphite",
        AccentShade(fill="#3F3F46", fill_hover="#27272A", on="#FFFFFF", text="#3F3F46", soft="#ECECEF"),
        AccentShade(fill="#5A5A63", fill_hover="#66666F", on="#FFFFFF", text="#D4D4DA", soft="#34343A")),
}


@dataclass(frozen=True)
class Band:
    """A colored header band, set by an organization theme pack."""
    bg: str
    text: str
    text2: str
    primary_bg: str
    primary_text: str


@dataclass(frozen=True)
class Header:
    """Resolved colors for title areas and command bars."""
    branded: bool
    bg: str
    bar: str
    text: str
    text2: str
    ink: str
    hover: str
    border: str
    button: str
    button_border: str
    primary_bg: str
    primary_text: str
    mark_bg: str
    mark_ink: str


@dataclass(frozen=True)
class Theme:
    mode: str
    palette: Palette
    accent: AccentShade
    accent_id: str
    header: Header

    @property
    def dark(self) -> bool:
        return self.mode == "dark"


def _header(palette: Palette, accent: AccentShade, band: Band | None, light_accent: AccentShade) -> Header:
    if band is None:
        return Header(
            branded=False, bg=palette.bg, bar=palette.surface, text=palette.text, text2=palette.text3,
            ink=palette.text2, hover=palette.hover, border=palette.border, button=palette.surface2,
            button_border=palette.border, primary_bg=accent.fill, primary_text=accent.on,
            mark_bg=accent.fill, mark_ink="#FFFFFF")
    return Header(
        branded=True, bg=band.bg, bar=band.bg, text=band.text, text2=band.text2, ink=band.text,
        hover=rgba(band.text, 0.14), border=rgba(band.text, 0.12), button=rgba(band.text, 0.08),
        button_border=rgba(band.text, 0.2), primary_bg=band.primary_bg, primary_text=band.primary_text,
        mark_bg=band.primary_bg, mark_ink=light_accent.fill)


def resolve(mode: str, accent: Accent, band: Band | None = None) -> Theme:
    if mode not in MODES:
        raise ValueError(f"unknown theme mode {mode!r}")
    palette = PALETTES[mode]
    shade = accent.shade(mode)
    return Theme(mode, palette, shade, accent.id, _header(palette, shade, band, accent.light))
