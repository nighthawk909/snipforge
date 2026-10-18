"""Capture presets: saved mode, delay, pointer, size, effects and destination, each with an optional
shortcut of its own (RegisterHotKey, never the keyboard hook), chosen from the capture toolbar, the
tray menu and Settings > Capture."""
import json
from dataclasses import replace

import pytest
from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeyEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from snipforge import countdown, prefs, presets, shortcuts
from snipforge.editor import beautify
from snipforge.presets import Preset
from snipforge.settings import app_settings
from snipforge.shortcuts import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT

KEYS = ("capture_mode", "capture_delay", "capture_pointer", "capture_fixed_size", "export_dir", "export_format",
        "copy_on_capture")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    def wipe():
        for name in KEYS:
            app_settings().remove(prefs.KEYS[name])
        app_settings().remove(presets.KEY)
        app_settings().remove(beautify.SETTING)
    wipe()
    monkeypatch.setattr(countdown, "SETTLE_MS", 0)
    yield
    wipe()


class FakeApi:
    def __init__(self, taken=()):
        self.taken = set(taken)
        self.registered: dict[int, tuple[int, int]] = {}

    def register(self, _hwnd, hotkey_id, mods, vk) -> bool:
        assert mods & MOD_NOREPEAT
        if (mods & ~MOD_NOREPEAT, vk) in self.taken or hotkey_id in self.registered:
            return False
        self.registered[hotkey_id] = (mods & ~MOD_NOREPEAT, vk)
        return True

    def unregister(self, _hwnd, hotkey_id) -> None:
        self.registered.pop(hotkey_id, None)


DOCS = Preset("d0c5", "Docs", mode="window", delay=0, pointer=True, beautify="on", border=3,
              torn=frozenset({"bottom"}), destination="copy", shortcut="Ctrl+Shift+1")


# --- the presets themselves ----------------------------------------------------------------------
def test_a_preset_is_saved_and_read_back_whole():
    presets.save([DOCS, Preset("f1", "Frame", mode="fixed", fixed_size=(1280, 720), destination="save",
                                folder="C:/Shots")])
    loaded = presets.load()
    assert loaded[0] == DOCS and loaded[1].fixed_size == (1280, 720) and loaded[1].folder == "C:/Shots"
    assert presets.get("f1").name == "Frame" and presets.get("nope") is None


def test_unusable_saved_presets_are_dropped_or_mended():
    app_settings().setValue(presets.KEY, json.dumps([
        {"id": "ok1", "name": "  Good   one ", "mode": "teleport", "delay": 7, "border": 99, "torn": ["up", "left"],
         "destination": "fax", "fixed_size": [5, 5], "pointer": "yes", "beautify": 1, "shortcut": 3},
        {"id": "ok1", "name": "Same id twice"}, {"id": "bad id!", "name": "x"}, {"id": "n0", "name": ""}, "junk"]))
    [mended] = presets.load()
    assert mended == Preset("ok1", "Good one", torn=frozenset({"left"}))
    app_settings().setValue(presets.KEY, "{not json")
    assert presets.load() == []


def test_saving_tells_whoever_listens():
    heard = []
    presets.changes().changed.connect(lambda: heard.append(True))
    presets.save([DOCS])
    assert heard


def test_a_summary_says_what_the_preset_does():
    assert DOCS.summary() == "Window · pointer · Beautify, border, torn edges · copy"
    assert Preset("x", "X", mode="fixed", fixed_size=(800, 600), delay=5).summary() == \
        "Fixed size 800 × 600 · 5 s delay · editor"
    assert DOCS.beautify_on(False) and not replace(DOCS, beautify="off").beautify_on(True)
    assert replace(DOCS, beautify="settings").beautify_on(True)


def test_torn_sides_round_trip_through_their_choices():
    for key in presets.TORN:
        assert Preset("t", "T", torn=presets.torn_sides(key)).torn_key == key


# --- shortcuts ----------------------------------------------------------------------------------
def test_a_preset_shortcut_is_a_registered_hotkey_of_its_own(qapp):
    api = FakeApi()
    keys = shortcuts.GlobalShortcuts(api)
    assert keys.set("snip", "Ctrl+Shift+X") is None
    assert keys.apply_presets([DOCS]) == {}
    assert sorted(api.registered.values()) == [(MOD_CONTROL | MOD_SHIFT, 0x31), (MOD_CONTROL | MOD_SHIFT, 0x58)]
    preset_id = keys._id(DOCS.action)
    assert preset_id >= shortcuts.FIRST_PRESET_ID and preset_id != keys._id("snip")
    fired = []
    keys.triggered.connect(fired.append)
    assert keys._hotkey(preset_id)
    assert fired == [DOCS.action] and presets.from_action(fired[0]) == "d0c5"
    keys.close()


def test_preset_shortcuts_are_checked_like_any_other(qapp):
    api = FakeApi(taken={(MOD_CONTROL | MOD_ALT, 0x77)})
    keys = shortcuts.GlobalShortcuts(api)
    keys.set("snip", "Ctrl+Shift+1")
    problems = keys.apply_presets([DOCS, Preset("b2", "Bug", shortcut="Ctrl+Alt+F8"),
                                   Preset("w3", "Hooked", shortcut="Meta+Shift+S")])
    assert "already the shortcut for New snip" in problems[DOCS.action]
    assert "Another app" in problems["preset:b2"] and "Win+Shift+S" in problems["preset:w3"]
    assert keys.set("preset:z9", "Ctrl+Shift+1", title="Zed") is not None
    keys.set("snip", "")
    assert keys.set(DOCS.action, "Ctrl+Shift+1", title="Docs") is None
    assert keys.set("snip", "Ctrl+Shift+1") == "Ctrl+Shift+1 is already the shortcut for the preset Docs."
    keys.close()


def test_a_deleted_preset_lets_go_of_its_shortcut(qapp):
    api = FakeApi()
    keys = shortcuts.GlobalShortcuts(api)
    keys.apply_presets([DOCS])
    assert api.registered
    keys.apply_presets([])
    assert not api.registered and DOCS.action not in keys.bindings()
    with pytest.raises(ValueError):
        keys.set("preset:", "Ctrl+Shift+2")


# --- the capture toolbar and the session -----------------------------------------------------------
def make_overlay(**kwargs):
    from snipforge.capture import _ScreenOverlay
    screen = QGuiApplication.primaryScreen()
    pix = QPixmap(screen.geometry().size())
    pix.fill(QColor("#e8eaee"))
    o = _ScreenOverlay(screen, pix, [QRect(100, 80, 300, 200)], toolbar=True, **kwargs)
    o.resize(screen.geometry().size())
    return o


def test_the_toolbar_offers_presets_only_when_there_are_some(qapp):
    plain = make_overlay()
    assert plain.toolbar.presets_button.isHidden()
    chosen = []
    o = make_overlay(presets=[("d0c5", "Docs"), ("b2", "Bug & fix")], preset="b2", recordable=True)
    o.preset_changed.connect(chosen.append)
    toolbar = o.toolbar
    assert not toolbar.presets_button.isHidden() and toolbar.presets_button.text().strip() == "Bug & fix"
    texts = [a.text() for a in toolbar.presets_menu.actions() if a.text()]
    assert texts == ["Your usual choices\t0", "Docs\t1", "Bug && fix\t2", "Manage presets…"]
    toolbar.presets_menu.actions()[2].trigger()
    QApplication.sendEvent(o, QKeyEvent(QEvent.KeyPress, Qt.Key_2, Qt.NoModifier, "2"))
    QApplication.sendEvent(o, QKeyEvent(QEvent.KeyPress, Qt.Key_0, Qt.NoModifier, "0"))
    QApplication.sendEvent(o, QKeyEvent(QEvent.KeyPress, Qt.Key_7, Qt.NoModifier, "7"))  # there's no 7th
    assert chosen == ["d0c5", "b2", ""]
    o.set_record(True)
    assert toolbar.presets_button.isHidden()  # presets are for snips
    plain.close()
    o.close()


def session_with(monkeypatch, **kwargs):
    from snipforge import capture
    results = []
    monkeypatch.setattr(capture.pointer, "grab", lambda: None)
    managed = []
    kwargs.setdefault("on_manage_presets", lambda: managed.append(True))
    session = capture.CaptureSession(results.append, **kwargs)
    session.managed = managed
    return session, results


def test_a_preset_snip_starts_with_its_choices_and_leaves_yours_alone(qapp, monkeypatch):
    prefs.set_capture_mode("area")
    prefs.set_capture_fixed_size(640, 480)
    frame = Preset("f1", "Frame", mode="fixed", fixed_size=(1280, 720), pointer=True)
    presets.save([frame])
    session, _ = session_with(monkeypatch, preset=frame)
    session.start()
    try:
        overlay = session._overlays[0]
        assert overlay.mode == "fixed" and overlay.fixed_size == (1280, 720)
        assert overlay.toolbar.presets_button.text().strip() == "Frame"
        overlay._pick_mode("window")
        overlay.toolbar.size_picked.emit(800, 600)
        assert prefs.capture_mode() == "area" and prefs.capture_fixed_size() == (640, 480)  # untouched
    finally:
        session._finish(None)


def test_a_preset_delay_counts_down_first(qapp, monkeypatch):
    waits = Preset("w1", "Menu", delay=3)
    session, _ = session_with(monkeypatch, preset=waits)
    session.start()
    try:
        assert session.countdown is not None and not session._overlays
        assert prefs.capture_delay() == 0
    finally:
        session._finish(None)


def test_picking_a_preset_on_the_toolbar_applies_it_and_your_usual_choices_come_back(qapp, monkeypatch):
    prefs.set_capture_mode("window")
    presets.save([Preset("f1", "Frame", mode="fixed", fixed_size=(1280, 720)), Preset("w1", "Menu", delay=5)])
    session, results = session_with(monkeypatch)
    session.start()
    try:
        overlay = session._overlays[0]
        overlay.toolbar.presets_menu.actions()[2].trigger()  # Frame
        assert session.preset.id == "f1" and overlay.mode == "fixed" and overlay.fixed_size == (1280, 720)
        overlay.toolbar.presets_menu.actions()[0].trigger()  # your usual choices
        assert session.preset is None and overlay.mode == "window"
        QApplication.sendEvent(overlay, QKeyEvent(QEvent.KeyPress, Qt.Key_2, Qt.NoModifier, "2"))  # Menu: 5 s
        assert session.countdown is not None and not session._overlays and results == []
        assert prefs.capture_delay() == 0 and prefs.capture_mode() == "window"
    finally:
        session._finish(None)


def test_picking_the_choice_in_use_changes_nothing(qapp, monkeypatch):
    prefs.set_capture_delay(3)
    presets.save([Preset("w1", "Menu", delay=5)])
    session, _ = session_with(monkeypatch)
    session.start()
    session.snip_now()  # past your usual 3 s
    QTest.qWait(20)
    try:
        overlays = list(session._overlays)
        QApplication.sendEvent(overlays[0], QKeyEvent(QEvent.KeyPress, Qt.Key_0, Qt.NoModifier, "0"))
        assert session._overlays == overlays and session.countdown is None  # no second countdown
        QApplication.sendEvent(overlays[0], QKeyEvent(QEvent.KeyPress, Qt.Key_1, Qt.NoModifier, "1"))
        assert session.countdown is not None  # Menu's 5 s
        session.snip_now()
        QTest.qWait(20)
        again = list(session._overlays)
        QApplication.sendEvent(again[0], QKeyEvent(QEvent.KeyPress, Qt.Key_1, Qt.NoModifier, "1"))
        assert session._overlays == again and session.countdown is None
    finally:
        session._finish(None)


def test_full_screen_picked_by_hand_still_waits_for_a_click(qapp, monkeypatch):
    session, results = session_with(monkeypatch, preset=Preset("a1", "Area"))
    session.start()
    try:
        overlay = session._overlays[0]
        QApplication.sendEvent(overlay, QKeyEvent(QEvent.KeyPress, Qt.Key_F, Qt.NoModifier, "f"))
        overlay.toolbar.delay_menu.actions()[1].trigger()  # 3 seconds, for this snip
        session.snip_now()
        QTest.qWait(20)
        assert results == [] and session._overlays and session._overlays[0].mode == "screen"
    finally:
        session._finish(None)


def test_recording_from_a_full_screen_preset_still_asks_for_the_area(qapp, monkeypatch):
    session, results = session_with(monkeypatch, preset=Preset("s1", "Screen", mode="screen"), record=True,
                                    on_area=lambda *a: None)
    session.start()
    try:
        assert results == [] and session._overlays
        assert session._overlays[0].toolbar.presets_button.isHidden()
    finally:
        session._finish(None)


def test_the_presets_button_keeps_its_width_and_the_hint_names_the_keys(qapp):
    o = make_overlay(presets=[("a", "Docs"), ("b", "A really very long preset name indeed")], preset="")
    button = o.toolbar.presets_button
    width = button.width()
    for pid in ("a", "b", ""):
        o.set_preset(pid)
        assert button.width() == width
    o.set_preset("b")
    assert button.text().strip() == "A really very lon…" and "long preset name indeed" in button.toolTip()
    assert "1-9 presets" in o.hint() and "1 to 9 for a capture preset" in o.accessibleDescription()
    assert "presets" not in make_overlay().hint()
    o.close()


def test_a_full_screen_preset_takes_the_monitor_at_once(qapp, monkeypatch):
    session, results = session_with(monkeypatch, preset=Preset("s1", "Screen", mode="screen"))
    session.start()
    screen = QGuiApplication.primaryScreen()
    assert len(results) == 1 and results[0].size() == screen.geometry().size() * screen.devicePixelRatio()
    assert session.preset.id == "s1"


def test_manage_presets_ends_the_snip_and_opens_settings(qapp, monkeypatch):
    presets.save([DOCS])
    session, results = session_with(monkeypatch)
    session.start()
    session._overlays[0].toolbar.presets_menu.actions()[-1].trigger()
    assert results == [None] and session.managed == [True] and not session._overlays


# --- the app: tray menu, shortcuts and where the snip goes ---------------------------------------------
@pytest.fixture
def forge(qapp, tmp_path, monkeypatch):
    from snipforge import app as app_module
    from snipforge.library import Library
    monkeypatch.setattr(app_module.HotkeyListener, "start", lambda self: True)
    monkeypatch.setattr(app_module.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_module.UpdateFlow, "start", lambda self: None)
    monkeypatch.setattr(app_module, "GlobalShortcuts", lambda: shortcuts.GlobalShortcuts(FakeApi()))
    f = app_module.SnipForgeApp(qapp, Library(tmp_path / "Library"))
    f.indexer._recognize = lambda image: ""
    f.notes = []
    monkeypatch.setattr(f, "_note", f.notes.append)
    yield f
    for window in list(f._windows):
        window.close()
    f.indexer.close()
    f._shortcuts.close()


def snip(width=200, height=100):
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor("#3366cc"))
    return image


class Taken:
    """A finished capture session, as the app sees it."""

    def __init__(self, preset):
        self.preset, self.source, self.note, self.area = preset, None, None, None


def test_the_tray_lists_presets_and_starts_a_snip_with_one(forge, monkeypatch):
    menu = forge._presets_menu
    assert not menu.menuAction().isVisible()  # none yet
    presets.save([DOCS])
    assert menu.menuAction().isVisible()
    assert [a.text() for a in menu.actions() if a.text()] == ["Docs\tCtrl+Shift+1", "Manage presets…"]
    started = []
    monkeypatch.setattr(forge, "_start", started.append)
    menu.actions()[0].trigger()
    assert started[0].preset == DOCS
    forge._session = None
    forge._on_shortcut(DOCS.action)  # its own shortcut does the same
    assert started[1].preset == DOCS


def test_the_saved_preset_shortcuts_are_registered_at_start(qapp, tmp_path, monkeypatch):
    presets.save([DOCS])
    from snipforge import app as app_module
    from snipforge.library import Library
    monkeypatch.setattr(app_module.HotkeyListener, "start", lambda self: True)
    monkeypatch.setattr(app_module.UpdateFlow, "start", lambda self: None)
    api = FakeApi()
    monkeypatch.setattr(app_module, "GlobalShortcuts", lambda: shortcuts.GlobalShortcuts(api))
    f = app_module.SnipForgeApp(qapp, Library(tmp_path))
    assert f._shortcuts.bindings() == {DOCS.action: "Ctrl+Shift+1"} and api.registered
    f.indexer.close()
    f._shortcuts.close()


def test_a_copy_preset_copies_the_finished_snip_and_keeps_it_editable(forge):
    forge._session = Taken(DOCS)  # border 3, torn bottom, Beautify on
    forge._on_captured(snip())
    copied = QGuiApplication.clipboard().image()
    pad = beautify.DEFAULT_STYLE.padding
    assert copied.size().toTuple() == (200 + 2 * pad, 100 + 2 * pad)  # Beautify around it
    assert copied.pixelColor(pad + 1, pad + 50).name() == "#1c1c1e"  # the border, inside the snip's edge
    assert not forge._windows and "Copied (Docs)" in forge.notes[0]
    [entry] = forge.library.entries()
    document = forge.library.load_document(entry)
    assert document["edges"] == {"border": 3, "color": "#1c1c1e", "torn": ["bottom"]}
    assert beautify.enabled() is False  # the preset's Beautify didn't change the setting


def test_a_save_preset_saves_in_its_folder_in_the_settings_format(forge, tmp_path):
    prefs.set_export_format("jpg")
    folder = tmp_path / "Shots"
    forge._session = Taken(Preset("s2", "Shots", destination="save", folder=str(folder)))
    forge._on_captured(snip())
    [saved] = list(folder.iterdir())
    assert saved.suffix == ".jpg" and QImage(str(saved)).size().toTuple() == (200, 100)
    assert str(saved) in forge.notes[0] and len(forge.library.entries()) == 1 and not forge._windows


def test_a_save_preset_without_a_folder_uses_the_save_folder(forge, tmp_path):
    prefs.set_export_dir(tmp_path / "Pictures")
    forge._session = Taken(Preset("s3", "Default", destination="save", beautify="off"))
    forge._on_captured(snip())
    assert [p.suffix for p in (tmp_path / "Pictures").iterdir()] == [".png"]


def test_a_preset_that_cannot_save_opens_the_snip_instead(forge, tmp_path):
    blocked = tmp_path / "a file"
    blocked.write_text("not a folder", encoding="utf-8")
    forge._session = Taken(Preset("s4", "Broken", destination="save", folder=str(blocked)))
    forge._on_captured(snip())
    assert "Couldn't save" in forge.notes[0] and len(forge._windows) == 1


def test_an_editor_preset_opens_with_its_edges_and_beautify(forge):
    forge._session = Taken(replace(DOCS, destination="editor", beautify="on"))
    forge._on_captured(snip())
    [editor] = forge._windows
    assert editor._beautify is True and beautify.enabled() is False
    assert editor.canvas.edges().border == 3 and editor.canvas.edges().torn == frozenset({"bottom"})


def test_copy_on_capture_copies_what_a_save_preset_saved(forge, tmp_path):
    prefs.set_copy_on_capture(True)
    forge._session = Taken(replace(DOCS, destination="save", folder=str(tmp_path)))  # border and Beautify
    forge._on_captured(snip())
    pad = beautify.DEFAULT_STYLE.padding
    assert QGuiApplication.clipboard().image().size().toTuple() == (200 + 2 * pad, 100 + 2 * pad)


def test_notes_never_promise_a_library_copy_that_failed(forge, monkeypatch):
    monkeypatch.setattr(forge.library, "create", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    forge._session = Taken(DOCS)
    forge._on_captured(snip())
    assert forge.notes == ["Copied (Docs)."]


def test_a_preset_shortcut_that_cannot_be_set_is_named_at_start(qapp, tmp_path, monkeypatch):
    presets.save([DOCS])
    from snipforge import app as app_module
    from snipforge.library import Library
    monkeypatch.setattr(app_module.HotkeyListener, "start", lambda self: True)
    monkeypatch.setattr(app_module.UpdateFlow, "start", lambda self: None)
    monkeypatch.setattr(app_module, "GlobalShortcuts",
                        lambda: shortcuts.GlobalShortcuts(FakeApi(taken={(MOD_CONTROL | MOD_SHIFT, 0x31)})))
    shown = []
    monkeypatch.setattr(app_module.QSystemTrayIcon, "showMessage", lambda self, *a: shown.append(a[1]))
    f = app_module.SnipForgeApp(qapp, Library(tmp_path))
    assert any("The preset Docs: Another app already uses Ctrl+Shift+1" in text for text in shown)
    f.indexer.close()
    f._shortcuts.close()


def test_presets_added_and_removed_never_reuse_a_hotkey_id(qapp):
    keys = shortcuts.GlobalShortcuts(FakeApi())
    seen = set()
    for n in range(5):
        preset = Preset(f"p{n}", f"P{n}", shortcut="Ctrl+Shift+F1")
        keys.apply_presets([preset])
        seen.add(keys._id(preset.action))
        keys.apply_presets([])
        assert preset.action not in keys._ids
    assert len(seen) == 5
    keys.close()


def test_the_raw_snip_is_not_copied_over_a_copy_preset(forge):
    prefs.set_copy_on_capture(True)
    forge._session = Taken(replace(DOCS, beautify="off", border=0, torn=frozenset()))
    forge._on_captured(snip(120, 40))
    assert QGuiApplication.clipboard().image().size().toTuple() == (120, 40)


# --- Settings > Capture ------------------------------------------------------------------------------
@pytest.fixture
def settings_window(qapp, tmp_path, monkeypatch):
    from snipforge import autostart
    from snipforge.settings_window import SettingsWindow
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    keys = shortcuts.GlobalShortcuts(FakeApi())
    window = SettingsWindow(library_root=tmp_path, shortcuts=keys)
    window.show_section("capture")
    yield window
    window.close()
    keys.close()


def fill(dialog_steps):
    """PresetDialog.exec, replaced: run the steps on the real dialog, then press its Save."""
    from snipforge.preset_settings import PresetDialog

    def run(self):
        dialog_steps(self)
        QTest.mouseClick(self.save_button, Qt.LeftButton)
        return self.result()
    return PresetDialog, "exec", run


def test_add_a_preset_from_settings(settings_window, monkeypatch):
    def steps(dialog):
        QTest.keyClicks(dialog.name, "Docs screenshot")
        QTest.mouseClick(dialog.mode.button("fixed"), Qt.LeftButton)
        assert not dialog.size_row.isHidden()
        dialog.width_box.setValue(1280)
        QTest.mouseClick(dialog.delay.button("3"), Qt.LeftButton)
        QTest.mouseClick(dialog.pointer, Qt.LeftButton)
        QTest.mouseClick(dialog.beautify.button("on"), Qt.LeftButton)
        QTest.mouseClick(dialog.torn.button("all"), Qt.LeftButton)
        QTest.mouseClick(dialog.destination.button("copy"), Qt.LeftButton)
        dialog.shortcut.changed.emit("Ctrl+Shift+2")
        dialog.shortcut.set_text("Ctrl+Shift+2")
    monkeypatch.setattr(*fill(steps))
    QTest.mouseClick(settings_window.preset_list.add_button, Qt.LeftButton)
    [made] = presets.load()
    assert (made.name, made.mode, made.fixed_size[0], made.delay, made.pointer, made.beautify, made.torn,
            made.destination, made.shortcut) == ("Docs screenshot", "fixed", 1280, 3, True, "on",
                                                 frozenset(presets.SIDES), "copy", "Ctrl+Shift+2")
    assert settings_window._shortcuts.bindings()[made.action] == "Ctrl+Shift+2"
    row = settings_window.preset_list.rows[made.id]
    assert row.title.text() == "Docs screenshot" and "Ctrl+Shift+2" in row.description.text()


def test_a_preset_needs_a_name_and_a_free_shortcut(settings_window, monkeypatch):
    settings_window._shortcuts.set("snip", "Ctrl+Shift+3")
    said = []

    def steps(dialog):
        QTest.mouseClick(dialog.save_button, Qt.LeftButton)
        said.append(dialog.problem.text())
        QTest.keyClicks(dialog.name, "Bug")
        dialog.shortcut.set_text("Ctrl+Shift+3")
        QTest.mouseClick(dialog.save_button, Qt.LeftButton)
        said.append(dialog.problem.text())
        dialog.shortcut.set_text("")
    monkeypatch.setattr(*fill(steps))
    settings_window.preset_list.add()
    assert said == ["Give the preset a name.", "Ctrl+Shift+3 is already the shortcut for New snip."]
    assert [p.name for p in presets.load()] == ["Bug"]


def test_change_and_delete_a_preset(settings_window, monkeypatch, tmp_path):
    presets.save([DOCS])
    settings_window.preset_list.refresh()
    settings_window._shortcuts.apply_presets([DOCS])
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: str(tmp_path))

    def steps(dialog):
        dialog.name.setText("Docs to a folder")
        QTest.mouseClick(dialog.destination.button("save"), Qt.LeftButton)
        assert not dialog.folder_row.isHidden()
        dialog._choose_folder()
    monkeypatch.setattr(*fill(steps))
    settings_window.preset_list.change(DOCS.id)
    changed = presets.get(DOCS.id)
    assert changed.name == "Docs to a folder" and changed.destination == "save" and changed.folder == str(tmp_path)
    asked = []
    monkeypatch.setattr(QMessageBox, "exec", lambda box: asked.append(box.textFormat()) or QMessageBox.Yes)
    settings_window.preset_list.delete(DOCS.id)
    assert asked == [Qt.PlainText]  # a name with <b> in it reads as typed
    assert presets.load() == [] and DOCS.action not in settings_window._shortcuts.bindings()
    assert settings_window.preset_list.rows == {}


def test_the_preset_window_only_holds_up_settings(settings_window, monkeypatch):
    from snipforge.preset_settings import PresetDialog
    seen = []
    monkeypatch.setattr(PresetDialog, "exec", lambda self: seen.append(self.windowModality()) or QDialog.Rejected)
    settings_window.preset_list.add()
    assert seen == [Qt.WindowModal]  # a snip started meanwhile gets the keyboard and mouse


def test_typing_a_preset_shortcut_steps_the_others_aside(settings_window):
    from snipforge.preset_settings import PresetDialog
    from tests.test_shortcuts import _focus, _key
    keys = settings_window._shortcuts
    keys.set("snip", "Ctrl+Shift+X")
    dialog = PresetDialog(Preset("n1", "New"), keys)
    _focus(dialog.shortcut.edit, True)
    assert keys._api.registered == {}  # pressing Ctrl+Shift+X now records it
    _key(dialog.shortcut.edit, Qt.Key_4, Qt.ControlModifier | Qt.ShiftModifier, "4")
    _focus(dialog.shortcut.edit, False)
    assert len(keys._api.registered) == 1 and dialog.shortcut.text() == "Ctrl+Shift+4"
    QTest.mouseClick(dialog.save_button, Qt.LeftButton)
    assert dialog.preset.shortcut == "Ctrl+Shift+4" and len(keys._api.registered) == 2
    dialog.close()


def test_a_preset_whose_shortcut_was_taken_can_still_be_changed(settings_window):
    from snipforge.preset_settings import PresetDialog
    keys = settings_window._shortcuts
    keys._api.taken.add((MOD_CONTROL | MOD_SHIFT, 0x31))  # another app has Ctrl+Shift+1 now
    dialog = PresetDialog(DOCS, keys)
    dialog.name.setText("Docs, renamed")
    QTest.mouseClick(dialog.save_button, Qt.LeftButton)
    assert dialog.preset is not None and dialog.preset.name == "Docs, renamed"
    assert dialog.preset.shortcut == "Ctrl+Shift+1"  # kept; the list says it isn't working
    dialog.close()


def test_cancelling_the_dialog_changes_nothing(settings_window, monkeypatch):
    from snipforge.preset_settings import PresetDialog
    monkeypatch.setattr(PresetDialog, "exec", lambda self: QDialog.Rejected)
    settings_window.preset_list.add()
    assert presets.load() == []


def test_manage_presets_shows_them_in_settings(forge, monkeypatch):
    forge.manage_presets()
    assert forge._settings.current_section() == "capture" and forge._settings.preset_list.isVisible()


def test_settings_finds_presets():
    from snipforge.settings_window import SECTIONS
    assert "preset" in SECTIONS["capture"][3]


def test_without_shortcuts_of_your_own_presets_have_no_shortcut_row(qapp):
    from snipforge.preset_settings import PresetDialog
    dialog = PresetDialog(DOCS, None)  # the Mac today: no GlobalShortcuts
    assert dialog.shortcut.parentWidget().isHidden()
    dialog.close()
