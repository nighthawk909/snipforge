"""Edit text (retype a line text recognition found: it's smart-erased and the new words are written
over it, matched in size and colour) and Smart Move (drag out part of the snip, put it anywhere;
Smart erase fills in where it was)."""
import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QTransform
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QInputDialog

from snipforge import ocr
from snipforge.editor import edit_text, piece
from snipforge.editor.piece import PieceItem
from snipforge.editor.text_items import TextItem
from tests.test_editor import drag, gesture, make_canvas, send


@pytest.fixture
def page(qapp):
    """A white page with a red block (to move) and a line of dark blue text drawn at 24 px."""
    img = QImage(400, 240, QImage.Format_ARGB32)
    img.fill(QColor("#ffffff"))
    p = QPainter(img)
    p.fillRect(40, 40, 60, 40, QColor("#e11d48"))
    font = QFont("Segoe UI")
    font.setPixelSize(24)
    p.setFont(font)
    p.setPen(QColor("#1e3a8a"))
    p.drawText(QPointF(40, 180), "Total due 120")
    p.end()
    return img


def kinds(canvas):
    return sorted(i.kind for i in canvas.annotations())


# --- Smart Move --------------------------------------------------------------------------------
def test_smart_move_picks_up_an_area_and_fills_in_where_it_was(page):
    canvas = make_canvas(page)
    canvas.set_tool("move")
    gesture(canvas, (35, 35), (105, 85))  # around the red block
    assert kinds(canvas) == ["erase", "piece"] and canvas.tool == "select"
    moved = next(i for i in canvas.annotations() if isinstance(i, PieceItem))
    assert moved.isSelected() and moved.scene_rect() == QRectF(35, 35, 70, 50)
    gesture(canvas, (70, 60), (270, 60))  # drag the piece 200 px right
    out = canvas.render_image()
    assert out.pixelColor(270, 60).name() == "#e11d48"  # the block, moved
    assert out.pixelColor(70, 60).name() != "#e11d48"  # filled in from the white around it
    canvas.undo_stack.undo()  # the drag
    canvas.undo_stack.undo()  # the Smart Move, all of it at once
    assert kinds(canvas) == []


def test_a_moved_piece_never_brings_back_what_was_hidden(page):
    canvas = make_canvas(page)
    drag(canvas, "blur", (30, 30), (110, 90))  # blurred first
    canvas.set_tool("move")
    gesture(canvas, (35, 35), (105, 85))
    moved = next(i for i in canvas.annotations() if isinstance(i, PieceItem))
    edge = moved.image().pixelColor(5, 25).name()  # the block's left edge, at (40, 60) on the snip
    assert edge not in ("#e11d48", "#ffffff")  # the piece holds the blurred pixels, not the sharp ones


def test_a_moved_piece_is_saved_and_turns_with_the_snip(page):
    canvas = make_canvas(page)
    canvas.set_tool("move")
    gesture(canvas, (35, 35), (105, 85))
    document = canvas.to_document()
    again = make_canvas(page)
    again.load_document(document)
    reopened = next(i for i in again.annotations() if isinstance(i, PieceItem))
    assert reopened.image().size().toTuple() == (70, 50)
    again.rotate_image(clockwise=True)
    turned = next(i for i in again.annotations() if isinstance(i, PieceItem))
    assert turned.image().size().toTuple() == (50, 70) and turned.scene_rect().size().toTuple() == (50.0, 70.0)


def test_a_damaged_piece_is_kept_unread_not_lost(page):
    canvas = make_canvas(page)
    canvas.load_document({"items": [{"kind": "piece", "pos": [0, 0], "size": [10, 10], "style": {},
                                     "image": "not a picture"}]})
    assert kinds(canvas) == [] and canvas.to_document()["items"][0]["image"] == "not a picture"


def test_turning_a_piece_picture():
    image = QImage(4, 2, QImage.Format_ARGB32)
    image.fill(QColor("#000000"))
    encoded = piece.encode(image)
    assert piece.decode(piece.turned(encoded, QTransform().rotate(90))).size().toTuple() == (2, 4)
    assert piece.turned(encoded, QTransform().scale(2, 2)) == encoded  # a resize leaves the picture
    with pytest.raises(ValueError):
        piece.decode("!!")


def test_smart_move_is_on_the_rail_and_on_g(qapp, page):
    from snipforge.editor.window import TOOLS
    assert any(t and t[0] == "move" for t in TOOLS)
    canvas = make_canvas(page)
    QTest.keyClick(canvas, Qt.Key_G)
    assert canvas.tool == "move"


# --- Edit text ---------------------------------------------------------------------------------
def line(text, rect):
    words, x = [], rect.left()
    for word in text.split():
        width = rect.width() * len(word) / len(text)
        words.append(ocr.OcrWord(word, QRectF(x, rect.top(), width, rect.height())))
        x += width + rect.width() / len(text)
    return ocr.OcrLine(text, tuple(words))


def test_colours_come_from_the_line_and_its_background(page):
    paper, ink = edit_text.colors(page, QRectF(38, 158, 170, 30))
    assert paper.name() == "#ffffff"
    assert abs(ink.blue() - 0x8a) < 40 and ink.red() < 80  # dark blue, near enough


def test_the_new_text_is_as_tall_as_the_old(qapp):
    size = edit_text.fitted_size("Total due 120", 17.0)
    height = QFontMetricsF(edit_text._font(size)).tightBoundingRect("Total due 120").height()
    assert 14 <= height <= 17


def test_edit_text_retypes_a_line_in_the_editor(qapp, page, tmp_path, monkeypatch):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library

    library = Library(tmp_path)
    editor = EditorWindow(page, library, library.create(page))
    editor.resize(900, 600)
    editor.show()
    found = ocr.OcrResult((line("Total due 120", QRectF(41, 162, 150, 22)),))
    monkeypatch.setattr(ocr, "recognize", lambda image: found)
    asked = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: asked.append(a[4]) or ("Total due 95", True))
    QTest.mouseClick(editor.commands.edit_text_button, Qt.LeftButton)
    canvas = editor.canvas
    assert canvas.choosing()
    send(canvas, "move", (100, 173))
    send(canvas, "press", (100, 173))  # click the line
    assert asked == ["Total due 120"] and not canvas.choosing()
    assert kinds(canvas) == ["erase", "text"]
    text = next(i for i in canvas.annotations() if isinstance(i, TextItem))
    assert text.toPlainText() == "Total due 95" and text.isSelected()
    assert not text.style.bold and not text.style.fill and abs(QColor(text.style.color).blue() - 0x8a) < 40
    assert 14 <= text.style.font_size <= 24
    canvas.undo_stack.undo()
    assert kinds(canvas) == []  # one step
    editor.close()


def test_edit_text_with_nothing_typed_just_removes_the_line(page):
    canvas = make_canvas(page)
    canvas.edit_line(QRectF(41, 162, 150, 22), "Total due 120", "  ")
    assert kinds(canvas) == ["erase"]


def test_esc_or_a_right_click_stops_choosing_and_a_click_beside_the_text_waits(page):
    canvas = make_canvas(page)
    chosen = []
    canvas.start_choosing([QRectF(41, 162, 150, 22)], chosen.append)
    send(canvas, "press", (300, 40))  # nowhere near
    assert canvas.choosing() and chosen == [] and kinds(canvas) == []
    QTest.keyClick(canvas, Qt.Key_Escape)
    assert not canvas.choosing()
    canvas.start_choosing([QRectF(41, 162, 150, 22)], chosen.append)
    QTest.mouseClick(canvas.viewport(), Qt.RightButton, Qt.NoModifier, QPoint(10, 10))
    assert not canvas.choosing() and chosen == []


def test_no_text_found_says_so(qapp, page, tmp_path, monkeypatch):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library

    library = Library(tmp_path)
    editor = EditorWindow(page, library, library.create(page))
    monkeypatch.setattr(ocr, "recognize", lambda image: ocr.OcrResult(()))
    editor.edit_text()
    assert not editor.canvas.choosing() and "No text" in editor.statusBar().currentMessage()
    editor.close()


def test_text_can_be_regular_weight(qapp):
    from snipforge.editor.style import Style
    regular = TextItem(Style(bold=False), "Hi")
    assert regular.font().weight() == QFont.Normal and TextItem(Style(), "Hi").font().weight() == QFont.Bold
    assert Style.from_dict({"bold": False}).bold is False and Style.from_dict({}).bold is True
