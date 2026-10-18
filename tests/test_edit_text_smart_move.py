"""Edit text (retype a line text recognition found: it's smart-erased and the new words are written
over it, matched in size and colour) and Smart Move (drag out part of the snip, put it anywhere;
Smart erase fills in where it was)."""
import json

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
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


def tight(text, size):
    return QFontMetricsF(edit_text._font(size)).tightBoundingRect(text)


def test_the_new_text_is_as_wide_as_the_old_unless_that_makes_it_far_taller(qapp):
    old = tight("Total due 120", 20)  # how the line looks at 20 px, in the font the editor uses
    assert edit_text.fitted_size("Total due 120", old.width() + 0.5, old.height()) == 20
    squeezed = edit_text.fitted_size("Total due 120", old.width() / 3, old.height())  # recognition's width is off
    assert abs(tight("Total due 120", squeezed).height() - old.height()) <= 0.25 * old.height()


def test_the_letters_are_found_even_when_recognition_puts_the_line_a_little_off(qapp):
    img = QImage(300, 80, QImage.Format_ARGB32)
    img.fill(QColor("#ffffff"))
    font = QFont("Segoe UI")
    font.setPixelSize(13)
    p = QPainter(img)
    p.setFont(font)
    p.setPen(QColor("#222222"))
    p.drawText(QPointF(20, 50), "Total due by Friday")
    p.end()
    ink = inked(img, img.rect())
    # Windows' recognition put 13 px Segoe UI's box 9.4 px above its letters at the top, 7 at the bottom
    recognized = ink.adjusted(0, -0.72 * ink.height(), 0, -0.54 * ink.height())
    found = edit_text.ink_box(img, recognized, QColor("#ffffff"))
    assert abs(found.top() - ink.top()) <= 1 and abs(found.bottom() - ink.bottom()) <= 1
    swap = edit_text.replacement(img, QPointF(0, 0), recognized, "Total due by Friday", QRectF(img.rect()))
    assert swap.erase.contains(ink)  # all of the old letters go
    blank = QImage(40, 40, QImage.Format_ARGB32)
    blank.fill(QColor("#ffffff"))
    assert edit_text.ink_box(blank, QRectF(5, 5, 20, 10), QColor("#ffffff")) == QRectF(5, 5, 20, 10)


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
    around = QRect(30, 140, 360, 60)
    old, new = inked(page, around), inked(canvas.render_image(), around)
    assert abs(new.height() - old.height()) <= 0.25 * old.height()  # as tall as the old letters
    assert abs(new.left() - old.left()) <= 2 and abs(new.bottom() - old.bottom()) <= 2  # where they were
    assert not editor.statusBar().currentMessage().startswith("Click the line")
    canvas.undo_stack.undo()
    assert kinds(canvas) == []  # one step
    editor.close()


def inked(image, area):
    """The pixels in `area` that aren't (nearly) white, as drawn."""
    xs, ys = [], []
    for y in range(area.top(), area.bottom() + 1):
        for x in range(area.left(), area.right() + 1):
            if sum(QColor(image.pixel(x, y)).getRgb()[:3]) < 3 * 255 - edit_text.INK_DIFFERENCE:
                xs.append(x)
                ys.append(y)
    return QRectF(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


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


# --- What the review found ---------------------------------------------------------------------
def test_the_new_text_and_a_moved_piece_stay_over_their_erase_after_a_reload(page):
    canvas = make_canvas(page)
    canvas.edit_line(QRectF(41, 162, 150, 22), "Total due 120", "Total due 95")
    canvas.set_tool("move")
    gesture(canvas, (35, 35), (105, 85))
    gesture(canvas, (70, 60), (270, 60))
    before = canvas.render_image()
    again = make_canvas(page)
    again.load_document(canvas.to_document())
    assert again.render_image() == before


def test_a_moved_piece_is_part_of_what_grab_text_reads(page):
    canvas = make_canvas(page)
    canvas.set_tool("move")
    gesture(canvas, (35, 35), (105, 85))
    gesture(canvas, (70, 60), (270, 60))
    drag(canvas, "rect", (300, 150), (380, 220))  # other markup isn't
    content = canvas.content_image()
    assert content.pixelColor(270, 60).name() == "#e11d48" and content.pixelColor(70, 60).name() != "#e11d48"
    assert content.pixelColor(300, 185).name() == "#ffffff"
    assert make_canvas(page).content_image() is not None


def test_a_piece_too_big_to_be_one_is_refused(qapp):
    big = QImage(2100, 2000, QImage.Format_ARGB32)  # over MAX_PIECE_PIXELS
    big.fill(QColor("#000000"))
    with pytest.raises(ValueError):
        piece.decode(piece.encode(big))
    kept = PieceItem(piece.default_style(), big)
    assert kept.image().width() * kept.image().height() <= piece.MAX_PIECE_PIXELS


def test_pasting_pieces_is_checked_like_other_pictures():
    from PySide6.QtCore import QSizeF

    from snipforge.editor import paste_checks
    one = {"kind": "piece", "pos": [0, 0], "size": [10, 10], "style": {}, "image": "x"}
    assert paste_checks.problem([one], QSizeF(100, 100)) is None
    assert paste_checks.problem([one] * (paste_checks.MAX_HEAVY + 1), QSizeF(100, 100))
    huge = {**one, "image": "x" * (paste_checks.MAX_PICTURES + 1)}
    assert paste_checks.problem([huge], QSizeF(100, 100))


def test_cutting_out_through_a_piece_cuts_its_picture_too(page):
    canvas = make_canvas(page)
    canvas.set_tool("move")
    gesture(canvas, (35, 35), (105, 85))  # a piece 70 px wide, at x 35 to 105
    canvas.cut_out("x", 50, 70)
    moved = next(i for i in canvas.annotations() if isinstance(i, PieceItem))
    assert moved.scene_rect().width() == 50 and moved.image().width() == 50
    canvas.cut_out("y", 30, 90)  # all of it
    moved = next(i for i in canvas.annotations() if isinstance(i, PieceItem))
    assert moved.image().pixelColor(0, 0).alpha() == 0
    canvas.undo_stack.undo()
    canvas.undo_stack.undo()
    moved = next(i for i in canvas.annotations() if isinstance(i, PieceItem))
    assert moved.image().width() == 70


def test_pieces_need_a_newer_document_than_plain_markup(page):
    from snipforge.editor.canvas import DOCUMENT_VERSION, UnsupportedDocument
    canvas = make_canvas(page)
    drag(canvas, "rect", (10, 10), (60, 60))
    assert canvas.to_document()["version"] == 1  # older versions still edit everything else
    canvas.set_tool("move")
    gesture(canvas, (35, 35), (105, 85))
    document = canvas.to_document()
    assert document["version"] == 2 == DOCUMENT_VERSION  # older ones open it read-only, as it looks
    make_canvas(page).load_document(document)
    with pytest.raises(UnsupportedDocument):
        make_canvas(page).load_document({**document, "version": 3})


def test_smart_move_says_so_when_it_cant_keep_the_picture(page, monkeypatch):
    canvas = make_canvas(page)
    said = []
    canvas.notice.connect(said.append)

    def fail(_image):
        raise ValueError("no memory")
    monkeypatch.setattr(piece, "encode", fail)
    canvas.set_tool("move")
    gesture(canvas, (35, 35), (105, 85))
    assert kinds(canvas) == [] and said and "memory" in said[-1]


def test_choosing_ends_when_the_snip_changes_or_the_eyedropper_starts(page):
    canvas = make_canvas(page)
    drag(canvas, "rect", (300, 150), (380, 220))
    canvas.start_choosing([QRectF(41, 162, 150, 22)], lambda n: None)
    canvas.undo_stack.undo()
    assert not canvas.choosing()
    canvas.start_choosing([QRectF(41, 162, 150, 22)], lambda n: None)
    canvas.rotate_image(clockwise=True)
    assert not canvas.choosing()
    canvas.start_choosing([QRectF(41, 162, 150, 22)], lambda n: None)
    canvas.start_picking()
    assert not canvas.choosing() and canvas.picking()
    canvas.start_choosing([QRectF(41, 162, 150, 22)], lambda n: None)
    assert canvas.choosing() and not canvas.picking()


def test_choosing_while_cropping_applies_the_crop_and_space_still_pans(page):
    canvas = make_canvas(page)
    canvas.set_tool("crop")
    canvas.start_choosing([QRectF(41, 162, 150, 22)], lambda n: None)
    assert canvas.tool == "select" and canvas.choosing()
    QTest.keyPress(canvas, Qt.Key_Space)
    assert canvas._panning and canvas.choosing()
    QTest.keyRelease(canvas, Qt.Key_Space)
    assert not canvas._panning and canvas.choosing()


def test_esc_clears_the_hint_to_click_a_line(qapp, page, tmp_path, monkeypatch):
    from snipforge.editor.window import CHOOSE_HINT, EditorWindow
    from snipforge.library import Library

    library = Library(tmp_path)
    editor = EditorWindow(page, library, library.create(page))
    found = ocr.OcrResult((line("Total due 120", QRectF(41, 162, 150, 22)),))
    monkeypatch.setattr(ocr, "recognize", lambda image: found)
    editor.edit_text()
    assert editor.statusBar().currentMessage() == CHOOSE_HINT
    QTest.keyClick(editor.canvas, Qt.Key_Escape)
    assert editor.statusBar().currentMessage() == ""
    editor.close()


def test_moving_a_piece_keeps_what_was_selected_selected(page):
    canvas = make_canvas(page)
    drag(canvas, "erase", (200, 20), (240, 60))  # so the snip is drawn with markup hidden, not just copied
    box = drag(canvas, "rect", (300, 150), (380, 220))
    box.setOpacity(0.4)
    assert box.isSelected()
    shown = canvas.shown_pixels()
    assert shown is not canvas.base_image and shown.pixelColor(300, 185).name() == "#ffffff"
    assert box.isSelected() and box.opacity() == 0.4


# --- What the second review found --------------------------------------------------------------
def stacked(pitch):
    """Three lines of 13 px text, `pitch` apart; their letters as drawn, each line's on its own."""
    img = QImage(320, 120, QImage.Format_ARGB32)
    img.fill(QColor("#ffffff"))
    font = QFont("Segoe UI")
    font.setPixelSize(13)
    texts = ["Total due by Friday 120", "Paid", "Questions? Ask the team"]
    letters = []
    for n, text in enumerate(texts):
        alone = QImage(img.size(), QImage.Format_ARGB32)
        alone.fill(QColor("#ffffff"))
        for target in (img, alone):
            p = QPainter(target)
            p.setFont(font)
            p.setPen(QColor("#222222"))
            p.drawText(QPointF(20, 40 + n * pitch), text)
            p.end()
        letters.append(inked(alone, alone.rect()))
    return img, texts, letters


@pytest.mark.parametrize("pitch", [13, 16, 20])
def test_a_box_between_two_lines_never_takes_in_the_other_line(qapp, pitch):
    img, texts, letters = stacked(pitch)
    middle = letters[1]
    for recognized in (middle, middle.adjusted(0, -0.72 * middle.height(), 0, -0.54 * middle.height())):
        swap = edit_text.replacement(img, QPointF(0, 0), recognized, texts[1], QRectF(img.rect()))
        assert swap.erase.contains(middle)
        assert not swap.erase.intersects(letters[0]) and not swap.erase.intersects(letters[2])


def test_a_busy_background_falls_back_to_the_box(qapp):
    img = QImage(200, 80, QImage.Format_ARGB32)
    p = QPainter(img)
    for y in range(80):
        p.fillRect(0, y, 200, 1, QColor.fromHsv(220, 40, 255 - 2 * y))
    p.end()
    box = QRectF(20, 24, 120, 24)
    assert edit_text.ink_box(img, box, edit_text.paper_color(img, box)) == box


def test_a_document_with_damaged_items_still_saves(page):
    canvas = make_canvas(page)
    canvas.load_document({"items": [5, None, "x"]})
    assert canvas.to_document()["items"] == [5, None, "x"]


def test_items_on_the_same_layer_keep_their_order_when_saved_copied_or_duplicated(page):
    canvas = make_canvas(page)
    canvas.set_tool("move")
    gesture(canvas, (35, 35), (105, 85))
    erase, moved = sorted(canvas.annotations(), key=lambda i: i.kind)
    moved.setZValue(erase.zValue())  # as a version without the fix saved them
    moved.setPos(moved.pos() + QPointF(200, 0))
    assert canvas.to_document()["items"][-1]["kind"] == "piece"  # drawn last, saved last
    canvas.set_tool("select")
    canvas._select([erase, moved])
    canvas.duplicate_selection()
    copies = canvas.selected_annotations()
    assert {i.kind: i.zValue() for i in copies}["piece"] > {i.kind: i.zValue() for i in copies}["erase"]
    canvas._select([erase, moved])
    assert json.loads(canvas.markup_data())["items"][-1]["kind"] == "piece"
    before = canvas.render_image()
    again = make_canvas(page)
    again.load_document(canvas.to_document())
    assert again.render_image() == before


def test_a_cut_through_a_stretched_piece_takes_every_column_it_touches(qapp):
    from snipforge.editor.cutout import Cut
    image = QImage(70, 10, QImage.Format_ARGB32)
    image.fill(QColor("#e11d48"))
    encoded = piece.encode(image)
    # drawn twice as wide: the cut's x 51 to 54 is the picture's 15.5 to 17
    assert piece.decode(piece.cut_out(encoded, QRectF(20, 0, 140, 10), Cut("x", 51, 54))).width() == 68
    assert piece.cut_out(encoded, QRectF(20, 0, 140, 10), Cut("x", 200, 220)) == encoded  # misses it


def test_pasted_pieces_are_counted_by_the_pixels_they_unpack_to():
    import base64
    import struct

    from PySide6.QtCore import QSizeF

    from snipforge.editor import paste_checks
    header = piece.PNG_HEAD + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 5000, 4000) + bytes(5)
    claimed = base64.b64encode(header).decode()  # a tiny paste that says it's 20 megapixels
    assert piece.pixels(claimed) == 20_000_000 and piece.pixels("nonsense") is None
    big = {"kind": "piece", "pos": [0, 0], "size": [10, 10], "style": {}, "image": claimed}
    assert paste_checks.problem([big], QSizeF(100, 100))


def test_a_double_click_while_choosing_is_a_click(page):
    canvas = make_canvas(page)
    chosen = []
    canvas.start_choosing([QRectF(41, 162, 150, 22)], chosen.append)
    view = canvas.mapFromScene(QPointF(300, 40))
    QTest.mouseDClick(canvas.viewport(), Qt.LeftButton, Qt.NoModifier, view)  # beside the line: still choosing
    assert canvas.choosing() and canvas.editing_text_item() is None
    QTest.mouseDClick(canvas.viewport(), Qt.LeftButton, Qt.NoModifier, canvas.mapFromScene(QPointF(100, 173)))
    assert chosen == [0] and canvas.editing_text_item() is None
