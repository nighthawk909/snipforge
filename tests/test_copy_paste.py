"""Copy, cut and paste markup, within a snip and between snips, and the canvas's right-click menu."""
import pytest
from PySide6.QtCore import QMimeData, QPointF
from PySide6.QtGui import QGuiApplication, QImage, QKeySequence
from PySide6.QtWidgets import QApplication

from snipforge.editor.canvas_clipboard import MARKUP_MIME, parse_markup
from snipforge.editor.window import EditorWindow
from snipforge.library import Library
from tests.test_editor import drag, make_canvas, send


@pytest.fixture
def editor(qapp, tmp_path, white_image):
    lib = Library(tmp_path)
    w = EditorWindow(white_image, lib, lib.create(white_image), on_new_snip=lambda: None)
    w.resize(1100, 700)
    w.show()
    QApplication.processEvents()
    yield w
    w.close()


def clipboard_with(fmt: str, data: bytes) -> None:
    mime = QMimeData()
    mime.setData(fmt, data)
    QGuiApplication.clipboard().setMimeData(mime)


def kinds(canvas):
    return sorted(i.kind for i in canvas.annotations())


def test_copy_keeps_the_snip_on_the_clipboard_and_adds_the_selected_markup(editor):
    arrow = drag(editor.canvas, "arrow", (20, 20), (120, 60))
    assert editor.canvas.selected_annotations() == [arrow]
    editor.copy_to_clipboard()
    mime = QGuiApplication.clipboard().mimeData()
    assert not QGuiApplication.clipboard().image().isNull()  # other apps still get the whole snip
    assert [s["kind"] for s in parse_markup(bytes(mime.data(MARKUP_MIME)))] == ["arrow"]
    assert "pastes the selected markup" in editor.statusBar().currentMessage()


def test_copy_with_nothing_selected_is_just_the_snip(editor):
    drag(editor.canvas, "arrow", (20, 20), (120, 60))
    editor.canvas._scene.clearSelection()
    editor.copy_to_clipboard()
    assert not QGuiApplication.clipboard().mimeData().hasFormat(MARKUP_MIME)
    assert editor.statusBar().currentMessage() == "Copied to clipboard"


def test_paste_in_the_same_snip_steps_down_and_right_each_time(editor):
    rect = drag(editor.canvas, "rect", (20, 20), (80, 60))
    editor.copy_to_clipboard()
    editor.paste()
    first = editor.canvas.selected_annotations()
    editor.paste()
    second = editor.canvas.selected_annotations()
    assert kinds(editor.canvas) == ["rect", "rect", "rect"]
    assert first[0].pos() == rect.pos() + QPointF(16, 16)
    assert second[0].pos() == rect.pos() + QPointF(32, 32)
    assert first[0].zValue() > rect.zValue()
    editor.canvas.undo_stack.undo()
    assert kinds(editor.canvas) == ["rect", "rect"]


def test_markup_pastes_into_another_snip_where_it_was(qapp, white_image, editor):
    callout = drag(editor.canvas, "callout", (20, 10), (150, 50))
    callout.text.setPlainText("Look here")
    editor.canvas.commit_text()
    editor.copy_to_clipboard()
    other = make_canvas(white_image)
    assert other.paste()
    pasted = other.annotations()
    assert [i.kind for i in pasted] == ["callout"]
    assert pasted[0].pos() == callout.pos() and pasted[0].plain_text() == "Look here"


@pytest.mark.parametrize("tool", ["arrow", "line", "rect", "ellipse", "pen", "highlight", "callout", "stamp",
                                  "pixelate", "blur", "spotlight", "magnify", "erase"])
def test_every_kind_of_markup_copies_and_pastes(qapp, white_image, tool):
    canvas = make_canvas(white_image)
    item = drag(canvas, tool, (30, 20), (130, 70))
    canvas.commit_text()
    canvas._select([item])
    QGuiApplication.clipboard().setMimeData(canvas.copy_mime(QImage()))
    assert canvas.paste()
    assert kinds(canvas) == [item.kind, item.kind]


def test_step_and_text_copy_and_paste(qapp, white_image):
    canvas = make_canvas(white_image)
    canvas.set_tool("step")
    send(canvas, "press", (60, 50))
    send(canvas, "release", (60, 50))
    canvas.set_tool("text")
    send(canvas, "press", (120, 40))
    send(canvas, "release", (120, 40))
    canvas.editing_text_item().setPlainText("Hi")
    canvas.commit_text()
    canvas.select_all()
    QGuiApplication.clipboard().setMimeData(canvas.copy_mime(QImage()))
    assert canvas.paste()
    assert kinds(canvas) == ["step", "step", "text", "text"]


def test_cut_removes_and_paste_brings_it_back(editor):
    rect = drag(editor.canvas, "rect", (20, 20), (80, 60))
    editor.cut()
    assert kinds(editor.canvas) == []
    assert editor.canvas.undo_stack.undoText() == "Cut"
    editor.paste()
    pasted = editor.canvas.annotations()
    assert kinds(editor.canvas) == ["rect"] and pasted[0].pos() == rect.pos()  # nothing there now to step off


def test_text_from_another_app_pastes_as_a_text_box(editor):
    QGuiApplication.clipboard().setText("Order #1234 shipped")
    editor.paste()
    [item] = editor.canvas.annotations()
    assert item.kind == "text" and item.toPlainText() == "Order #1234 shipped"
    assert editor.canvas.crop_rect().contains(item.sceneBoundingRect().center())


def test_paste_while_typing_goes_into_the_text(editor):
    QGuiApplication.clipboard().setText(" world")
    editor.canvas.set_tool("text")
    send(editor.canvas, "press", (100, 50))
    send(editor.canvas, "release", (100, 50))
    editing = editor.canvas.editing_text_item()
    editing.setPlainText("Hello")
    cursor = editing.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    editing.setTextCursor(cursor)
    editor.paste()
    assert editing.toPlainText() == "Hello world"
    assert len(editor.canvas.annotations()) == 1


def test_damaged_or_foreign_markup_pastes_nothing(editor):
    notices = []
    editor.canvas.notice.connect(notices.append)
    for data in (b"not json", b'{"version": 1, "items": [{"kind": "rocket"}]}', b'{"version": 9, "items": []}',
                 b'{"version": 1, "items": [{"kind": "rect", "pos": "nope"}]}'):
        clipboard_with(MARKUP_MIME, data)
        assert not editor.canvas.paste()
    assert editor.canvas.annotations() == [] and editor.canvas.undo_stack.count() == 0
    assert notices and "isn't markup" in notices[-1]


def test_a_picture_on_the_clipboard_says_why_it_cant_paste(editor):
    notices = []
    editor.canvas.notice.connect(notices.append)
    image = QImage(10, 10, QImage.Format_RGB32)
    image.fill(0)
    QGuiApplication.clipboard().setImage(image)
    assert not editor.canvas.paste()
    assert "picture" in notices[-1]


def test_markup_copied_off_this_snip_lands_where_you_can_see_it(qapp, white_image):
    big = QImage(800, 600, QImage.Format_RGB32)
    big.fill(0xFFFFFF)
    source = make_canvas(big)
    drag(source, "rect", (600, 450), (700, 520))
    QGuiApplication.clipboard().setMimeData(source.copy_mime(QImage()))
    small = make_canvas(white_image)  # 200 x 100
    assert small.paste()
    assert small.annotations()[0].sceneBoundingRect().intersects(small.crop_rect())


def test_right_click_menu_on_markup(editor):
    canvas = editor.canvas
    rect = drag(canvas, "rect", (20, 20), (80, 60))
    canvas.apply_style(fill=True)
    canvas._scene.clearSelection()
    menu = canvas.context_menu(canvas.mapFromScene(QPointF(50, 40)))
    assert canvas.selected_annotations() == [rect]  # right-clicking markup selects it
    actions = {a.text(): a for a in menu.actions() if a.text()}
    assert list(actions) == ["Cut", "Copy", "Paste", "Duplicate", "Delete", "Bring to front", "Send to back",
                             "Select all"]
    assert actions["Cut"].shortcut() == QKeySequence("Ctrl+X")
    actions["Duplicate"].trigger()
    assert kinds(canvas) == ["rect", "rect"]
    actions["Copy"].trigger()  # the window copies: the snip plus the selection
    assert QGuiApplication.clipboard().mimeData().hasFormat(MARKUP_MIME)


def test_right_click_menu_on_the_snip(editor):
    canvas = editor.canvas
    QGuiApplication.clipboard().clear()
    menu = canvas.context_menu(canvas.mapFromScene(QPointF(100, 50)))
    actions = {a.text(): a for a in menu.actions() if a.text()}
    assert "Copy snip" in actions and actions["Copy snip"].isEnabled()
    assert not actions["Cut"].isEnabled() and not actions["Paste"].isEnabled()


def test_right_click_cancelling_the_eyedropper_shows_no_menu(editor, monkeypatch):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QContextMenuEvent, QMouseEvent
    canvas = editor.canvas
    asked = []
    monkeypatch.setattr(canvas, "context_menu", lambda pos: asked.append(pos))  # None: no menu, never blocks
    canvas.start_picking()
    view = QPointF(canvas.mapFromScene(QPointF(100, 50)))
    QApplication.sendEvent(canvas.viewport(), QMouseEvent(QEvent.MouseButtonPress, view, view, Qt.RightButton,
                                                          Qt.RightButton, Qt.NoModifier))
    assert not canvas.picking()
    def right_click_menu():
        point = view.toPoint()
        event = QContextMenuEvent(QContextMenuEvent.Mouse, point, canvas.viewport().mapToGlobal(point))
        QApplication.sendEvent(canvas.viewport(), event)

    right_click_menu()
    assert asked == []  # this right-click only cancelled picking
    right_click_menu()
    assert len(asked) == 1  # the next one gets its menu
