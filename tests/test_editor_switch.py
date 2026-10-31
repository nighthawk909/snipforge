"""One editor that changes snip in place: a new snip, a Recent snip or one from the Library shows in
the editor that's open (its position, size, tool and panels stay put), the last few snips keep
their undo history, and the Recent strip holds still with the open snip marked."""
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QKeySequence, QWheelEvent
from PySide6.QtWidgets import QApplication

from snipforge.editor.snip_switch import KEEP_UNDO_FOR
from tests.test_editor import drag
from tests.test_integration import forge  # noqa: F401 - the tray app fixture


def snip(color: str) -> QImage:
    img = QImage(200, 100, QImage.Format_ARGB32)
    img.fill(QColor(color))
    return img


def editors(app):
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    return [w for w in app._windows if type(w).__name__ == "EditorWindow" and w.isVisible()]


def take(app, *colors):
    """Snip each color in turn; returns the one editor."""
    for color in colors:
        app._on_captured(snip(color))
    (editor,) = editors(app)
    return editor


def shown_color(editor) -> str:
    return editor.canvas.render_image().pixelColor(5, 5).name()


def test_a_new_snip_shows_in_the_editor_thats_open(forge):  # noqa: F811
    first = take(forge, "#ff0000")
    first.setGeometry(140, 90, 1200, 720)  # bigger than its least size with the test's wide stand-in font
    QApplication.processEvents()
    geometry = first.geometry()
    red = first.entry_id()
    drag(first.canvas, "rect", (20, 20), (80, 60))
    forge._on_captured(snip("#00ff00"))
    (editor,) = editors(forge)
    assert editor is first and editor.geometry() == geometry and shown_color(editor) == "#00ff00"
    older = next(e for e in forge.library.entries() if e.id == red)
    assert forge.library.load_document(older)["items"]  # its rectangle was saved as it was left


def test_a_recent_snip_shows_in_place_and_the_strip_holds_still(forge):  # noqa: F811
    editor = take(forge, "#ff0000", "#00ff00", "#0000ff")
    order = editor.tray.thumb_ids()
    assert order[0] == editor.entry_id()  # the open snip is in the strip, first (newest)
    assert editor.tray.current_id() == editor.entry_id()
    oldest = order[-1]
    editor.tray.thumb(oldest).click()
    assert editors(forge) == [editor] and editor.entry_id() == oldest and shown_color(editor) == "#ff0000"
    assert editor.tray.thumb_ids() == order  # nothing moved
    assert editor.tray.thumb(oldest).isChecked() and not editor.tray.thumb(order[0]).isChecked()


def test_going_back_keeps_your_undo(forge):  # noqa: F811
    editor = take(forge, "#ff0000")
    red = editor.entry_id()
    box = drag(editor.canvas, "rect", (20, 20), (80, 60))
    forge._on_captured(snip("#00ff00"))
    forge.open_entry(red)
    assert editor.entry_id() == red and editor.canvas.undo_stack.canUndo()
    editor.canvas.undo_stack.undo()
    assert box.scene() is None  # the rectangle you drew before switching is undone


def test_only_the_last_few_snips_keep_their_undo(forge):  # noqa: F811
    editor = take(forge, "#ff0000")
    first = editor.entry_id()
    drag(editor.canvas, "rect", (20, 20), (80, 60))
    for n in range(KEEP_UNDO_FOR):
        forge._on_captured(snip(f"#0000{n + 10:02x}"))
    assert len(editor.kept_snips()) == KEEP_UNDO_FOR and first not in editor.kept_snips()
    forge.open_entry(first)
    assert editor.entry_id() == first
    assert [i.kind for i in editor.canvas.annotations()] == ["rect"]  # its markup, from the Library
    assert not editor.canvas.undo_stack.canUndo()


def test_the_tool_and_panel_stay_as_they_were(forge):  # noqa: F811
    editor = take(forge, "#ff0000", "#00ff00")
    editor.canvas.set_tool("arrow")
    editor.canvas.apply_style(color="#bf5af2")
    older = editor.tray.thumb_ids()[1]
    editor.tray.thumb(older).click()
    assert editor.canvas.tool == "arrow" and editor.canvas.tool_styles["arrow"].color == "#bf5af2"
    assert editor.windowTitle().startswith("SnipForge")


def press(editor, shortcut: str) -> None:
    """The editor's action for this shortcut, as pressing it does."""
    (action,) = [a for a in editor.actions() if a.shortcut() == QKeySequence(shortcut)]
    action.trigger()


def test_ctrl_page_keys_step_through_snips(forge):  # noqa: F811
    editor = take(forge, "#ff0000", "#00ff00", "#0000ff")
    newest_first = editor.tray.thumb_ids()
    press(editor, "Ctrl+PgDown")
    assert editor.entry_id() == newest_first[1]
    press(editor, "Ctrl+PgDown")
    assert editor.entry_id() == newest_first[2]
    press(editor, "Ctrl+PgDown")  # the oldest: stays
    assert editor.entry_id() == newest_first[2]
    press(editor, "Ctrl+PgUp")
    assert editor.entry_id() == newest_first[1]


def test_the_wheel_scrolls_the_strip_and_arrows_show_when_it_overflows(forge):  # noqa: F811
    editor = take(forge, *[f"#{n:02x}0000" for n in range(10, 22)])
    editor.resize(900, 700)
    QApplication.processEvents()
    QApplication.processEvents()  # the strip lays its thumbs out a pass after the window's layout
    tray = editor.tray
    assert tray.can_scroll()
    bar = tray.strip_scroll_bar()
    start = bar.value()
    pos = QPointF(10, 10)
    wheel = QWheelEvent(pos, tray.strip().mapToGlobal(pos), QPoint(), QPoint(0, -120), Qt.NoButton,
                        Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(tray.strip().viewport(), wheel)
    assert bar.value() > start
    assert tray.scroll_buttons_visible()


def test_a_new_snip_appears_at_the_front_of_the_strip(forge):  # noqa: F811
    editor = take(forge, "#ff0000", "#00ff00")
    before = editor.tray.thumb_ids()
    forge._on_captured(snip("#0000ff"))
    after = editor.tray.thumb_ids()
    assert after[1:] == before and after[0] == editor.entry_id()


def test_leaving_a_snip_reads_its_words_again_for_search(forge, monkeypatch):  # noqa: F811
    seen = []
    monkeypatch.setattr(forge.indexer, "index", lambda entry, again=False: seen.append((entry.id, again)))
    editor = take(forge, "#ff0000")
    red = editor.entry_id()
    drag(editor.canvas, "rect", (20, 20), (80, 60))  # changed: its words may be covered now
    forge._on_captured(snip("#00ff00"))
    assert (red, True) in seen


def test_a_snip_that_cant_autosave_opens_in_a_window_of_its_own(forge, white_image, no_dialogs):  # noqa: F811
    editor = take(forge, "#ff0000")
    shown = editor.entry_id()
    broken = forge.library.create(white_image)
    (broken.path / "document.json").write_text("{broken", encoding="utf-8")
    forge.open_entry(broken.id)
    windows = editors(forge)
    assert len(windows) == 2 and editor.entry_id() == shown  # the open editor kept its snip


def test_closing_the_editor_saves_every_snip_it_kept(forge):  # noqa: F811
    editor = take(forge, "#ff0000")
    red = editor.entry_id()
    drag(editor.canvas, "rect", (20, 20), (80, 60))
    forge._on_captured(snip("#00ff00"))
    drag(editor.canvas, "ellipse", (20, 20), (80, 60))
    green = editor.entry_id()
    editor.close()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    by_id = {e.id: e for e in forge.library.entries()}
    assert [i["kind"] for i in forge.library.load_document(by_id[red])["items"]] == ["rect"]
    assert [i["kind"] for i in forge.library.load_document(by_id[green])["items"]] == ["ellipse"]


# --- review: nothing lost, nothing stale, nothing rewritten -----------------------------------
def test_a_snip_that_couldnt_be_saved_as_you_left_it_is_kept_and_saved_later(forge, monkeypatch):  # noqa: F811
    editor = take(forge, "#ff0000")
    red = editor.entry_id()
    drag(editor.canvas, "rect", (20, 20), (80, 60))
    real_save = forge.library.save

    def held_by_antivirus(*_a, **_k):
        raise OSError("the file is in use")
    monkeypatch.setattr(forge.library, "save", held_by_antivirus)
    for n in range(KEEP_UNDO_FOR + 1):  # more snips than it keeps: the unsaved one isn't let go
        forge._on_captured(snip(f"#00{n + 10:02x}00"))
    assert red in editor.kept_snips()
    monkeypatch.setattr(forge.library, "save", real_save)
    editor.close()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    by_id = {e.id: e for e in forge.library.entries()}
    assert [i["kind"] for i in forge.library.load_document(by_id[red])["items"]] == ["rect"]


def test_looking_at_a_snip_doesnt_rewrite_it_or_read_it_again(forge, monkeypatch):  # noqa: F811
    editor = take(forge, "#ff0000", "#00ff00")
    saved, read = [], []
    monkeypatch.setattr(forge.library, "save", lambda entry, *_a: saved.append(entry.id))
    monkeypatch.setattr(forge.indexer, "index", lambda entry, again=False: read.append(entry.id))
    older = editor.tray.thumb_ids()[1]
    editor.tray.thumb(older).click()
    editor.tray.thumb(editor.tray.thumb_ids()[0]).click()
    assert saved == [] and read == []  # nothing changed, so nothing is written or read again


def test_edit_texts_choosing_stops_when_you_switch(forge):  # noqa: F811
    editor = take(forge, "#ff0000", "#00ff00")
    left_behind = editor.canvas
    left_behind.start_choosing([left_behind.sceneRect()], lambda n: None)
    editor.tray.thumb(editor.tray.thumb_ids()[1]).click()
    assert not left_behind.choosing()


def test_a_snip_taken_while_a_dialog_is_open_gets_its_own_window(forge):  # noqa: F811
    from PySide6.QtWidgets import QDialog
    editor = take(forge, "#ff0000")
    shown = editor.entry_id()
    dialog = QDialog(editor)
    dialog.setModal(True)
    dialog.show()  # Save As, Resize or Edit text waiting on you
    QApplication.processEvents()
    forge._on_captured(snip("#00ff00"))
    assert editor.entry_id() == shown and len(editors(forge)) == 2
    dialog.close()


def test_the_thumbnail_shows_what_you_changed(forge):  # noqa: F811
    editor = take(forge, "#ff0000", "#00ff00")
    shown = editor.entry_id()
    before = editor.tray.thumb(shown).icon().cacheKey()
    drag(editor.canvas, "rect", (20, 20), (180, 90))
    editor.tray.thumb(editor.tray.thumb_ids()[1]).click()  # leave it: it's saved
    assert editor.tray.thumb(shown).icon().cacheKey() != before


def test_newer_markup_opens_in_a_window_of_its_own(forge, white_image, no_dialogs):  # noqa: F811
    import json
    editor = take(forge, "#ff0000")
    shown = editor.entry_id()
    newer = forge.library.create(white_image)
    (newer.path / "document.json").write_text(json.dumps({"version": 99, "items": []}), encoding="utf-8")
    forge.open_entry(newer.id)
    assert editor.entry_id() == shown and len(editors(forge)) == 2



def test_step_numbers_and_select_all_steps_follow_the_snip_on_show(forge):  # noqa: F811
    from tests.test_editor import gesture

    editor = take(forge, "#ff0000")
    red = editor.entry_id()
    editor.canvas.set_tool("step")
    for x in (20, 50, 80):
        gesture(editor.canvas, (x, 30), (x, 30))
    assert editor.panel.step_controls().next.text() == "4"
    forge._on_captured(snip("#00ff00"))
    green_canvas = editor.canvas
    assert editor.panel.step_controls().next.text() == "1"  # a new snip counts from 1
    gesture(green_canvas, (20, 30), (20, 30))
    green_selection = green_canvas.selected_annotations()
    forge.open_entry(red)
    assert editor.panel.step_controls().next.text() == "4"  # and the red one carries on where it was
    editor.panel.step_controls().select_all.click()
    assert len(editor.canvas.selected_annotations()) == 3  # the red snip's steps, not the green one's
    assert green_canvas.selected_annotations() == green_selection
