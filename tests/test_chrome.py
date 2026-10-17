"""Editor command bar and recent tray."""
from snipforge.editor.chrome import CommandBar, Tray
from snipforge.library import Library


def test_command_bar_buttons_emit_their_commands(qapp):
    bar = CommandBar()
    fired = []
    for name in ("new_capture", "undo", "redo", "zoom_out", "zoom_in", "fit", "grab_text", "redact", "pin",
                 "save", "copy", "toggle_theme"):
        getattr(bar, name).connect(lambda n=name: fired.append(n))
    bar.new_button.click()
    bar.copy_button.click()
    bar.theme_button.click()
    bar.undo_button.click()
    assert fired == ["new_capture", "copy", "toggle_theme", "undo"]


def test_command_bar_takes_extra_buttons_before_share(qapp):
    from PySide6.QtWidgets import QPushButton
    bar = CommandBar()
    extra = QPushButton("Drag out")
    bar.insert_before_share(extra, "drag")
    row = bar.layout()
    assert row.indexOf(extra) == row.indexOf(bar.share_button) - 1
    assert not extra.icon().isNull()


def test_command_bar_shows_zoom_and_history(qapp):
    bar = CommandBar()
    bar.set_zoom(0.876)
    assert bar.zoom_label.text() == "88%"
    bar.set_history(can_undo=False, can_redo=True)
    assert not bar.undo_button.isEnabled() and bar.redo_button.isEnabled()


def test_tray_lists_recent_snips_and_opens_them(qapp, tmp_path, white_image):
    lib = Library(tmp_path)
    entries = [lib.create(white_image) for _ in range(3)]
    tray = Tray()
    opened = []
    tray.open_entry.connect(opened.append)
    tray.set_recent(entries)
    assert tray.thumb_count() == 3
    tray._thumbs.itemAt(1).widget().click()
    assert opened == [entries[1].id]
    tray.set_image_size(200, 100)
    tray.status.showMessage("Copied to clipboard")
    assert tray.status.currentMessage() == "Copied to clipboard"
