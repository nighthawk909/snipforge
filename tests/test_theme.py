"""Theme tokens, theme packs, stylesheet generation and the theme manager."""
import json

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from snipforge import theme
from snipforge.settings import app_settings
from snipforge.theme import packs, styles, titlebar, tokens
from snipforge.theme.manager import ACCENT_KEY, MODE_KEY, ThemeManager

CENCORA = {
    "schema": 1,
    "id": "cencora",
    "name": "Cencora",
    "default": True,
    "accent": {
        "light": {"fill": "#461E96", "fill_hover": "#3A1880", "on": "#FFFFFF", "text": "#461E96", "soft": "#EEE9FA"},
        "dark": {"fill": "#5B3CC4", "fill_hover": "#6B4FD0", "on": "#FFFFFF", "text": "#B6A9F2", "soft": "#2F2754"},
    },
    "band": {"bg": "#211359", "text": "#FFFFFF", "text2": "#CBC4EE",
             "primary_bg": "#FFFFFF", "primary_text": "#211359"},
}


def cencora(**changes) -> dict:
    data = json.loads(json.dumps(CENCORA))
    data.update(changes)
    return data


# ---------------------------------------------------------------------- tokens
def test_rgba_uses_integer_alpha_for_qt_stylesheets():
    assert tokens.rgba("#FF8000", 0.5) == "rgba(255, 128, 0, 128)"
    assert tokens.rgba("#000000", 0) == "rgba(0, 0, 0, 0)"


def test_contrast_ratio_matches_wcag_reference_values():
    assert tokens.contrast_ratio("#FFFFFF", "#000000") == pytest.approx(21.0, abs=0.01)
    assert tokens.contrast_ratio("#777777", "#FFFFFF") == pytest.approx(4.48, abs=0.01)


@pytest.mark.parametrize("mode", tokens.MODES)
def test_builtin_text_colors_are_readable(mode):
    pal = tokens.PALETTES[mode]
    for ground in (pal.bg, pal.surface, pal.surface2):
        for ink in (pal.text, pal.text2, pal.text3):
            assert tokens.contrast_ratio(ink, ground) >= 4.5, (mode, ink, ground)


@pytest.mark.parametrize("accent", list(tokens.BUILTIN_ACCENTS.values()), ids=lambda a: a.id)
@pytest.mark.parametrize("mode", tokens.MODES)
def test_builtin_accents_are_readable(accent, mode):
    shade = accent.shade(mode)
    assert tokens.contrast_ratio(shade.on, shade.fill) >= 4.5
    assert tokens.contrast_ratio(shade.on, shade.fill_hover) >= 4.5
    assert tokens.contrast_ratio(shade.text, shade.soft) >= 4.5
    assert tokens.contrast_ratio(shade.text, tokens.PALETTES[mode].surface) >= 4.5


def test_resolve_picks_palette_and_accent_shade_for_the_mode():
    accent = tokens.BUILTIN_ACCENTS["snipforge"]
    light, dark = tokens.resolve("light", accent), tokens.resolve("dark", accent)
    assert light.palette is tokens.LIGHT and dark.palette is tokens.DARK
    assert light.accent == accent.light and dark.accent == accent.dark
    assert not light.dark and dark.dark


def test_resolve_rejects_unknown_mode():
    with pytest.raises(ValueError):
        tokens.resolve("sepia", tokens.BUILTIN_ACCENTS["snipforge"])


def test_header_without_band_blends_into_the_window():
    t = tokens.resolve("light", tokens.BUILTIN_ACCENTS["snipforge"])
    assert not t.header.branded
    assert t.header.bg == t.palette.bg
    assert t.header.primary_bg == t.accent.fill


def test_header_with_band_uses_band_colors_and_translucent_controls():
    pack = packs.parse_pack(CENCORA)
    t = tokens.resolve("dark", pack.accent, pack.band)
    assert t.header.branded
    assert t.header.bg == "#211359"
    assert t.header.primary_bg == "#FFFFFF" and t.header.primary_text == "#211359"
    assert t.header.hover.startswith("rgba(255, 255, 255,")


def test_theme_is_immutable():
    t = tokens.resolve("light", tokens.BUILTIN_ACCENTS["snipforge"])
    with pytest.raises(AttributeError):
        t.mode = "dark"


# ---------------------------------------------------------------------- packs
def test_parse_pack_reads_accent_band_and_default():
    pack = packs.parse_pack(CENCORA)
    assert pack.accent.id == "cencora" and pack.accent.name == "Cencora"
    assert pack.accent.light.fill == "#461E96"
    assert pack.band.bg == "#211359"
    assert pack.default


def test_parse_pack_band_is_optional():
    data = cencora()
    del data["band"]
    assert packs.parse_pack(data).band is None


@pytest.mark.parametrize("change, message", [
    ({"schema": 2}, "schema"),
    ({"id": "Bad Id!"}, "id"),
    ({"name": ""}, "name"),
    ({"name": "x" * 41}, "name"),
    ({"accent": {"light": CENCORA["accent"]["light"]}}, "dark"),
    ({"default": "yes"}, "default"),
])
def test_parse_pack_rejects_invalid_fields(change, message):
    with pytest.raises(packs.ThemePackError, match=message):
        packs.parse_pack(cencora(**change))


def test_parse_pack_rejects_colors_that_are_not_plain_hex():
    data = cencora()
    data["accent"]["light"]["fill"] = "url(http://example.com/x.png)"
    with pytest.raises(packs.ThemePackError, match="fill"):
        packs.parse_pack(data)


def test_parse_pack_rejects_unreadable_combinations():
    data = cencora()
    data["accent"]["light"]["on"] = "#5A3AA0"  # purple text on a purple button
    with pytest.raises(packs.ThemePackError, match="contrast"):
        packs.parse_pack(data)


@pytest.mark.parametrize("field, value", [("fill", "#461E96\n"), ("on", " #FFFFFF")])
def test_parse_pack_rejects_colors_with_stray_whitespace(field, value):
    data = cencora()
    data["accent"]["light"][field] = value
    with pytest.raises(packs.ThemePackError, match=field):
        packs.parse_pack(data)


@pytest.mark.parametrize("change", [{"id": "cencora\n"}, {"schema": True}])
def test_parse_pack_rejects_near_misses(change):
    with pytest.raises(packs.ThemePackError):
        packs.parse_pack(cencora(**change))


def test_parse_pack_rejects_non_objects():
    with pytest.raises(packs.ThemePackError):
        packs.parse_pack(["not", "a", "pack"])


def test_load_pack_rejects_bad_json_and_huge_files(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ nope", encoding="utf-8")
    with pytest.raises(packs.ThemePackError, match="JSON"):
        packs.load_pack(bad)
    huge = tmp_path / "huge.json"
    huge.write_text(" " * (packs.MAX_PACK_BYTES + 1), encoding="utf-8")
    with pytest.raises(packs.ThemePackError, match="large"):
        packs.load_pack(huge)


def test_load_pack_accepts_a_byte_order_mark(tmp_path):
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(CENCORA).encode("utf-8"))
    assert packs.load_pack(path).accent.id == "cencora"


@pytest.mark.parametrize("content", ["[" * 30000 + "]" * 30000, "1" * 5000], ids=["deep", "huge-number"])
def test_hostile_json_is_a_pack_error_not_a_crash(tmp_path, content):
    path = tmp_path / "evil.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(packs.ThemePackError):
        packs.load_pack(path)
    assert packs.discover_packs([tmp_path]) == []


def test_discover_packs_skips_bad_files_duplicates_and_builtin_ids(tmp_path, caplog):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    (first / "cencora.json").write_text(json.dumps(CENCORA), encoding="utf-8")
    (second / "cencora.json").write_text(json.dumps(cencora(name="Second copy")), encoding="utf-8")
    (second / "broken.json").write_text("{", encoding="utf-8")
    (second / "clash.json").write_text(json.dumps(cencora(id="snipforge")), encoding="utf-8")
    (second / "notes.txt").write_text("ignored", encoding="utf-8")
    found = packs.discover_packs([first, second, tmp_path / "missing"])
    assert [p.accent.name for p in found] == ["Cencora"]
    assert "broken.json" in caplog.text and "snipforge" in caplog.text


# ---------------------------------------------------------------------- styles
def test_stylesheet_is_built_from_theme_tokens():
    light = tokens.resolve("light", tokens.BUILTIN_ACCENTS["snipforge"])
    dark = tokens.resolve("dark", tokens.BUILTIN_ACCENTS["teal"])
    light_qss, dark_qss = styles.build_stylesheet(light), styles.build_stylesheet(dark)
    assert light.palette.bg in light_qss and light.accent.fill in light_qss
    assert dark.palette.bg in dark_qss and dark.accent.fill in dark_qss
    assert light_qss != dark_qss
    assert light_qss.count("{") == light_qss.count("}")


def test_stylesheet_parses_with_awkward_profile_paths(qapp, tmp_path):
    """Image paths are quoted, so a profile like O'Brien or José doesn't break the whole theme."""
    from PySide6.QtCore import qInstallMessageHandler

    odd = tmp_path / "José O'Brien (x)"
    odd.mkdir()
    assets = {}
    for name in ("check", "up", "down"):
        path = odd / f"{name}.svg"
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"/>', encoding="utf-8")
        assets[name] = str(path)
    messages = []
    previous = qInstallMessageHandler(lambda _mode, _ctx, msg: messages.append(msg))
    try:
        qapp.setStyleSheet(styles.build_stylesheet(tokens.resolve("light", tokens.BUILTIN_ACCENTS["teal"]), assets))
        QWidget().ensurePolished()
    finally:
        qInstallMessageHandler(previous)
        qapp.setStyleSheet("")
    assert not [m for m in messages if "parse" in m.lower()], messages


@pytest.mark.parametrize(("installed", "family"), [
    ({"Segoe UI Variable Text", "Segoe UI"}, "Segoe UI Variable Text"),  # Windows 11
    ({"Segoe UI"}, "Segoe UI"),  # Windows 10
    (set(), "Segoe UI"),  # no fonts to ask about (Qt's offscreen platform)
])
def test_interface_font_is_the_one_family_installed(monkeypatch, installed, family):
    """One family, never a list: a font with a list of families makes Qt look up every fallback
    font the first time it's used, which took the Library window 0.3 s to build on a laptop and up
    to 7.8 s on GitHub's Windows runner."""
    class FontDatabase:
        @staticmethod
        def hasFamily(name):  # noqa: N802 - Qt API
            return name in installed

    monkeypatch.setattr(styles, "QFontDatabase", FontDatabase)
    font = styles.ui_font()
    assert font.families() == [family]
    assert font.pointSizeF() == 10


def test_palette_maps_tokens_to_qt_roles(qapp):
    t = tokens.resolve("dark", tokens.BUILTIN_ACCENTS["snipforge"])
    pal = styles.build_palette(t)
    assert pal.color(QPalette.Window) == QColor(t.palette.bg)
    assert pal.color(QPalette.Base) == QColor(t.palette.surface)
    assert pal.color(QPalette.Highlight) == QColor(t.accent.fill)
    assert pal.color(QPalette.Link) == QColor(t.accent.text)


# ---------------------------------------------------------------------- title bar
def test_colorref_is_bgr():
    assert titlebar.colorref("#112233") == 0x00332211


def test_style_title_bar_ignores_invalid_windows():
    assert titlebar.style_title_bar(0, dark=True, caption="#000000", text="#FFFFFF") is False


# ---------------------------------------------------------------------- manager
@pytest.fixture
def fresh_prefs():
    s = app_settings()
    s.remove(MODE_KEY)
    s.remove(ACCENT_KEY)
    yield s
    s.remove(MODE_KEY)
    s.remove(ACCENT_KEY)


@pytest.fixture
def make_manager(qapp, fresh_prefs):
    made = []

    def make(**kwargs):
        kwargs.setdefault("packs", [])
        m = ThemeManager(qapp, **kwargs)
        made.append(m)
        return m

    yield make
    for m in made:
        m.close()
    qapp.setStyleSheet("")
    qapp.setPalette(QPalette())


def test_defaults_follow_windows_and_the_default_accent(make_manager):
    m = make_manager()
    assert m.mode_preference == "system"
    assert m.accent_id == "snipforge"
    assert m.theme.mode in tokens.MODES


def test_set_mode_persists_applies_and_emits(make_manager, qapp, fresh_prefs):
    m = make_manager()
    seen = []
    m.changed.connect(seen.append)
    m.set_mode_preference("dark")
    assert fresh_prefs.value(MODE_KEY) == "dark"
    assert m.theme.mode == "dark" and seen[-1] is m.theme
    assert tokens.DARK.bg in qapp.styleSheet()
    assert qapp.palette().color(QPalette.Window) == QColor(tokens.DARK.bg)


def test_set_accent_switches_colors(make_manager, fresh_prefs):
    m = make_manager()
    m.set_mode_preference("light")
    m.set_accent("teal")
    assert fresh_prefs.value(ACCENT_KEY) == "teal"
    assert m.theme.accent == tokens.BUILTIN_ACCENTS["teal"].light


def test_invalid_preferences_are_rejected(make_manager):
    m = make_manager()
    with pytest.raises(ValueError):
        m.set_mode_preference("sepia")
    with pytest.raises(ValueError):
        m.set_accent("no-such-accent")


def test_corrupt_saved_values_fall_back_to_defaults(make_manager, fresh_prefs):
    fresh_prefs.setValue(MODE_KEY, "purple")
    fresh_prefs.setValue(ACCENT_KEY, "missing")
    m = make_manager()
    assert m.mode_preference == "system"
    assert m.accent_id == "snipforge"


def test_pack_marked_default_is_used_until_the_user_chooses(make_manager):
    pack = packs.parse_pack(CENCORA)
    m = make_manager(packs=[pack])
    assert m.accent_id == "cencora"
    assert m.theme.header.branded
    assert [a.id for a in m.accents()][-1] == "cencora"
    m.set_accent("snipforge")
    assert not m.theme.header.branded


def test_no_change_means_no_signal(make_manager):
    m = make_manager()
    m.set_mode_preference("light")
    seen = []
    m.changed.connect(seen.append)
    m.set_mode_preference("light")
    assert seen == []


def test_follows_windows_when_set_to_system(make_manager):
    class Hints:
        scheme = None

        def colorScheme(self):  # noqa: N802 - mirrors Qt
            return self.scheme

    m = make_manager()
    m.set_mode_preference("system")
    real, fake = m._hints, Hints()
    m._hints = fake
    try:
        fake.scheme = Qt.ColorScheme.Dark
        m._on_system_scheme(fake.scheme)
        assert m.theme.mode == "dark"
        fake.scheme = Qt.ColorScheme.Light
        m._on_system_scheme(fake.scheme)
        assert m.theme.mode == "light"
    finally:
        m._hints = real


def test_fixed_choice_ignores_windows(make_manager):
    m = make_manager()
    m.set_mode_preference("dark")
    m._on_system_scheme(Qt.ColorScheme.Light)
    assert m.theme.mode == "dark"


def test_shown_windows_get_title_bar_colors_but_popups_do_not(make_manager, monkeypatch):
    from PySide6.QtWidgets import QMenu

    calls = []
    monkeypatch.setattr(titlebar, "style_title_bar", lambda hwnd, dark, caption, text: calls.append((dark, caption)))
    m = make_manager()
    m.set_mode_preference("dark")
    window = QWidget()
    window.show()
    assert calls[-1] == (True, m.theme.header.bg)
    count = len(calls)
    menu = QMenu()
    menu.addAction("x")
    menu.show()
    assert len(calls) == count
    menu.close()
    window.close()


def test_theme_images_fall_back_when_the_cache_folder_is_unusable(make_manager, monkeypatch, tmp_path):
    from snipforge.theme import manager as manager_module

    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(manager_module, "ASSET_DIR", blocker / "theme")
    m = make_manager()
    assert m._assets() == {}
    m.set_mode_preference("dark")  # still applies, just without the little images
    assert "image: url" not in QApplication.instance().styleSheet()


def test_broken_pack_discovery_falls_back_to_built_in_accents(qapp, fresh_prefs, monkeypatch):
    from snipforge.theme import manager as manager_module

    def explode(_folders):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(manager_module, "discover_packs", explode)
    m = ThemeManager(qapp)
    try:
        assert [a.id for a in m.accents()] == list(tokens.BUILTIN_ACCENTS)
    finally:
        m.close()


def test_install_is_idempotent_and_changes_reach_open_windows(qapp, fresh_prefs, monkeypatch, tmp_path,
                                                              white_image):
    from PySide6.QtCore import QObject

    from snipforge.editor.window import EditorWindow
    from snipforge.home import HomeWindow
    from snipforge.library import Library
    from snipforge.theme import manager as manager_module

    monkeypatch.setattr(theme, "_manager", None)
    monkeypatch.setattr(manager_module, "pack_folders", lambda: [])
    font = qapp.font()
    first = theme.install(qapp)
    try:
        assert theme.install(qapp) is first and theme.get_manager() is first

        class Probe(QObject):
            def __init__(self):
                super().__init__()
                self.seen = []

            def slot(self, t):
                self.seen.append(t.mode)

        probe = Probe()
        theme.on_change(probe.slot)
        lib = Library(tmp_path)
        editor, home = EditorWindow(white_image, lib, None), HomeWindow(lib, lambda: None, lambda _i: None)
        first.set_mode_preference("dark")
        assert probe.seen[-1] == "dark"
        assert editor.canvas.backgroundBrush().color() == QColor(tokens.DARK.well)
        for w in (editor, home):
            w.setAttribute(Qt.WA_DeleteOnClose)
            w.close()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        first.set_mode_preference("light")  # the windows are gone; nothing may touch them
        assert probe.seen[-1] == "light"
    finally:
        first.close()
        qapp.setStyleSheet("")
        qapp.setPalette(QPalette())
        qapp.setFont(font)


def test_current_returns_a_default_theme_without_a_manager():
    assert theme.get_manager() is None
    assert theme.current().mode == "light"
    assert theme.current().accent == tokens.BUILTIN_ACCENTS["snipforge"].light


def test_windows_take_their_look_from_the_app_stylesheet(qapp, tmp_path, white_image):
    from snipforge.editor.window import EditorWindow
    from snipforge.home import HomeWindow
    from snipforge.library import Library

    lib = Library(tmp_path)
    for window in (HomeWindow(lib, lambda: None, lambda _id: None), EditorWindow(white_image, lib, None)):
        offenders = [w for w in [window, *window.findChildren(QWidget)]
                     if w.styleSheet() and "background:" in w.styleSheet().replace(" ", "")]
        assert offenders == [], [o.objectName() or type(o).__name__ for o in offenders]
        window.close()
    QApplication.processEvents()


# ---------------------------------------------------------------------- build
def test_build_ships_valid_theme_packs_and_rejects_bad_ones(tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("sf_build", Path(__file__).parents[1] / "scripts" / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    app_dir, source = tmp_path / "SnipForge", tmp_path / "themes"
    app_dir.mkdir()
    assert build.add_theme_packs(app_dir, source) == []  # public builds have no packs
    source.mkdir()
    (source / "cencora.json").write_text(json.dumps(CENCORA), encoding="utf-8")
    assert [p.name for p in build.add_theme_packs(app_dir, source)] == ["cencora.json"]
    assert (app_dir / "themes" / "cencora.json").exists()
    (source / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(SystemExit, match="broken.json"):
        build.add_theme_packs(app_dir, source)
