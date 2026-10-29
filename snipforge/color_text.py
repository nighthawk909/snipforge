"""A color written down to paste somewhere else: HEX (#2F5BEA), RGB (rgb(47, 91, 234)) or HSL
(hsl(226, 82%, 55%)), as CSS and design tools take them. Pick a color (color_picker.py) copies one."""
from __future__ import annotations

from PySide6.QtGui import QColor

from .prefs import COLOR_FORMATS as FORMATS

LABELS = {"hex": "HEX", "rgb": "RGB", "hsl": "HSL"}


def hex_text(color: QColor) -> str:
    return color.name().upper()


def rgb_text(color: QColor) -> str:
    return f"rgb({color.red()}, {color.green()}, {color.blue()})"


def hsl(color: QColor) -> tuple[int, int, int]:
    """Hue in whole degrees (0-359), saturation and lightness in whole percents. Worked out from the
    8-bit values, not QColor's own HSL (which rounds to 0-255 steps first)."""
    r, g, b = color.red() / 255, color.green() / 255, color.blue() / 255
    high, low = max(r, g, b), min(r, g, b)
    lightness = (high + low) / 2
    spread = high - low
    if spread == 0:
        return 0, 0, round(lightness * 100)
    saturation = spread / (1 - abs(2 * lightness - 1))
    if high == r:
        hue = ((g - b) / spread) % 6
    elif high == g:
        hue = (b - r) / spread + 2
    else:
        hue = (r - g) / spread + 4
    return round(hue * 60) % 360, round(saturation * 100), round(lightness * 100)


def hsl_text(color: QColor) -> str:
    h, s, light = hsl(color)
    return f"hsl({h}, {s}%, {light}%)"


def as_text(color: QColor, fmt: str) -> str:
    """`color` written in `fmt` (one of FORMATS)."""
    writers = {"hex": hex_text, "rgb": rgb_text, "hsl": hsl_text}
    if fmt not in writers:
        raise ValueError(f"unknown color format {fmt!r}")
    return writers[fmt](color)


def summary(color: QColor, copied: str = "") -> str:
    """HEX and RGB, for a notice ("HEX #2F5BEA · RGB 47, 91, 234"), leaving out the one `copied`."""
    parts = {"hex": f"HEX {hex_text(color)}", "rgb": f"RGB {color.red()}, {color.green()}, {color.blue()}"}
    return " · ".join(text for fmt, text in parts.items() if fmt != copied)


def next_format(fmt: str, step: int = 1) -> str:
    """The format after `fmt` (before it, with step -1), going round."""
    if fmt not in FORMATS:
        return FORMATS[0]
    return FORMATS[(FORMATS.index(fmt) + step) % len(FORMATS)]
