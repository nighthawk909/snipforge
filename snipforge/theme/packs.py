"""Theme packs: JSON files that add an accent (and optionally a header band).

Organizations ship one with their build (in a `themes` folder next to SnipForge.exe), or a person
drops one into their own themes folder. Packs are data only: every value is validated, colors
must be plain #RRGGBB and readable, and a bad pack is skipped with a log message, never fatal.

    {"schema": 1, "id": "acme", "name": "Acme", "default": true,
     "accent": {"light": {fill, fill_hover, on, text, soft}, "dark": {...}},
     "band": {bg, text, text2, primary_bg, primary_text}}      # band is optional
"""
from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, fields
from pathlib import Path

from .tokens import BUILTIN_ACCENTS, HEX, PALETTES, Accent, AccentShade, Band, contrast_ratio

log = logging.getLogger(__name__)

SCHEMA = 1
MAX_PACK_BYTES = 64 * 1024
MAX_NAME = 40
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")  # use with fullmatch
MIN_CONTRAST = 4.5


class ThemePackError(ValueError):
    pass


@dataclass(frozen=True)
class ThemePack:
    accent: Accent
    band: Band | None
    default: bool
    source: Path | None = None


def _color(data: dict, key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ThemePackError(f"{where}.{key} must be a color like #1A2B3C")
    return value.upper()


def _readable(fg: str, bg: str, what: str) -> None:
    if contrast_ratio(fg, bg) < MIN_CONTRAST:
        raise ThemePackError(f"{what} doesn't have enough contrast to read ({contrast_ratio(fg, bg):.1f}:1, "
                             f"needs {MIN_CONTRAST}:1)")


def _shade(data, mode: str) -> AccentShade:
    where = f"accent.{mode}"
    if not isinstance(data, dict):
        raise ThemePackError(f"{where} is missing")
    shade = AccentShade(**{f.name: _color(data, f.name, where) for f in fields(AccentShade)})
    _readable(shade.on, shade.fill, f"{where}: 'on' over 'fill'")
    _readable(shade.on, shade.fill_hover, f"{where}: 'on' over 'fill_hover'")
    _readable(shade.text, shade.soft, f"{where}: 'text' over 'soft'")
    _readable(shade.text, PALETTES[mode].surface, f"{where}: 'text' over the {mode} background")
    return shade


def _band(data) -> Band | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ThemePackError("band must be an object")
    band = Band(**{f.name: _color(data, f.name, "band") for f in fields(Band)})
    _readable(band.text, band.bg, "band: 'text' over 'bg'")
    _readable(band.text2, band.bg, "band: 'text2' over 'bg'")
    _readable(band.primary_text, band.primary_bg, "band: 'primary_text' over 'primary_bg'")
    return band


def parse_pack(data, source: Path | None = None) -> ThemePack:
    if not isinstance(data, dict):
        raise ThemePackError("a theme pack must be a JSON object")
    schema = data.get("schema")
    if isinstance(schema, bool) or schema != SCHEMA:
        raise ThemePackError(f"unsupported schema {schema!r} (expected {SCHEMA})")
    pack_id = data.get("id")
    if not isinstance(pack_id, str) or not ID.fullmatch(pack_id):
        raise ThemePackError("id must be 1-32 lowercase letters, digits or dashes")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME:
        raise ThemePackError(f"name must be 1-{MAX_NAME} characters")
    default = data.get("default", False)
    if not isinstance(default, bool):
        raise ThemePackError("default must be true or false")
    accent = data.get("accent")
    if not isinstance(accent, dict):
        raise ThemePackError("accent is missing")
    light, dark = _shade(accent.get("light"), "light"), _shade(accent.get("dark"), "dark")
    return ThemePack(Accent(pack_id, name.strip(), light, dark), _band(data.get("band")), default, source)


def load_pack(path: Path) -> ThemePack:
    try:
        too_big = path.stat().st_size > MAX_PACK_BYTES
    except OSError as exc:
        raise ThemePackError(f"can't read the file: {exc}") from exc
    if too_big:
        raise ThemePackError(f"file is too large (over {MAX_PACK_BYTES // 1024} KB)")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))  # tolerate a BOM (Notepad, PowerShell)
    except (OSError, UnicodeDecodeError) as exc:
        raise ThemePackError(f"can't read the file: {exc}") from exc
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:  # deep nesting, giant numbers
        raise ThemePackError(f"not valid JSON: {type(exc).__name__}") from exc
    return parse_pack(data, path)


def discover_packs(folders: Iterable[Path]) -> list[ThemePack]:
    """Every valid pack in these folders, in order; the first pack with an id wins."""
    found: dict[str, ThemePack] = {}
    for folder in folders:
        try:
            files = sorted(p for p in folder.glob("*.json") if p.is_file())
        except OSError:
            continue
        for path in files:
            try:
                pack = load_pack(path)
            except ThemePackError as exc:
                log.warning("Skipping theme pack %s: %s", path.name, exc)
                continue
            except Exception:  # a pack must never stop SnipForge from starting
                log.exception("Skipping theme pack %s", path.name)
                continue
            if pack.accent.id in BUILTIN_ACCENTS:
                log.warning("Skipping theme pack %s: id %r is built in", path.name, pack.accent.id)
            elif pack.accent.id in found:
                log.info("Skipping theme pack %s: %r was already loaded", path.name, pack.accent.id)
            else:
                found[pack.accent.id] = pack
    return list(found.values())


def pack_folders() -> list[Path]:
    """Where packs come from: shipped with the installed app (for IT builds; next to SnipForge.exe),
    then the user's own folder."""
    from PySide6.QtCore import QStandardPaths

    from ..bundle import resources_dir

    folders = []
    if getattr(sys, "frozen", False):
        folders.append(resources_dir() / "themes")
    user = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
    if user:
        folders.append(Path(user) / "SnipForge" / "themes")
    return folders
