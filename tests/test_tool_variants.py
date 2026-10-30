"""Tool variants: arrow heads and curves, shapes, step number styles, highlight boxes, text
formatting, rounded redactions and the spotlight's dimming. Each is a style field; a snip that uses
one needs document version 3, so an older SnipForge opens it read-only instead of drawing it wrong."""
import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage

from snipforge.editor.canvas import DOCUMENT_VERSION, UnsupportedDocument, document_version
from snipforge.editor.factory import create_item
from snipforge.editor.items import ArrowItem, EffectItem, RectItem, SpotlightItem, StepItem, roman
from snipforge.editor.style import (
    ARROW_HEADS,
    BADGES,
    NUMBERINGS,
    SHAPES,
    STYLE_FIELDS,
    Style,
    needs_version_3,
)
from snipforge.editor.text_items import TextItem
from tests.test_editor import drag, gesture, make_canvas


@pytest.fixture
def page(qapp):
    img = QImage(240, 160, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    return img


# ---------------------------------------------------------------- the fields
def test_new_fields_have_quiet_defaults_and_validate():
    s = Style()
    assert (s.heads, s.open_head, s.curve, s.corner) == ("end", False, False, -1)
    assert (s.numbering, s.badge, s.box, s.font_family) == ("digits", "circle", False, "")
    assert (s.align, s.italic, s.underline, s.outline, s.rounded, s.dim) == ("", False, False, False, False, -1)
    cleaned = Style.from_dict({"heads": "both", "open_head": True, "curve": True, "corner": 12.7, "numbering": "roman",
                               "badge": "square", "box": True, "font_family": "Georgia", "align": "center",
                               "italic": True, "underline": True, "outline": True, "rounded": True, "dim": 80})
    assert cleaned.heads == "both" and cleaned.open_head and cleaned.curve and cleaned.corner == 13
    assert cleaned.numbering == "roman" and cleaned.badge == "square" and cleaned.box
    assert cleaned.font_family == "Georgia" and cleaned.align == "center" and cleaned.dim == 80
    bad = Style.from_dict({"heads": "sideways", "corner": -5, "numbering": 3, "badge": "", "font_family": 7,
                           "align": "justify", "dim": 500, "shape": "hexagon"})
    assert bad == Style(dim=100)  # numbers clamp (a negative corner is the usual one), words fall back


def test_rect_shapes_and_callout_shapes_share_the_field():
    assert "star" in SHAPES and "rounded" in SHAPES and "diamond" in SHAPES
    assert Style.from_dict({"shape": "star"}).shape == "star"
    assert "shape" in STYLE_FIELDS["rect"] and "shape" in STYLE_FIELDS["callout"]
    assert "shape" not in STYLE_FIELDS["ellipse"] and "shape" in STYLE_FIELDS["spotlight"]


def test_each_tool_shows_only_its_own_variants():
    assert {"heads", "open_head", "curve"} <= set(STYLE_FIELDS["arrow"])
    assert "heads" not in STYLE_FIELDS["line"] and "curve" in STYLE_FIELDS["line"]
    assert "corner" in STYLE_FIELDS["rect"] and "corner" not in STYLE_FIELDS["ellipse"]
    assert {"numbering", "badge"} <= set(STYLE_FIELDS["step"])
    assert "box" in STYLE_FIELDS["highlight"]
    assert {"font_family", "align", "italic", "underline", "outline", "bold"} <= set(STYLE_FIELDS["text"])
    assert {"font_family", "align", "italic", "underline"} <= set(STYLE_FIELDS["callout"])
    assert "rounded" in STYLE_FIELDS["blur"] and "rounded" in STYLE_FIELDS["pixelate"]
    assert "dim" in STYLE_FIELDS["spotlight"] and "rounded" in STYLE_FIELDS["spotlight"]
    assert set(ARROW_HEADS) == {"end", "start", "both"} and set(BADGES) == {"circle", "square", "pill"}
    assert set(NUMBERINGS) == {"digits", "letters", "roman"}


# ---------------------------------------------------------------- versions
def test_a_snip_using_a_variant_needs_version_3(page):
    assert DOCUMENT_VERSION == 3
    plain = {"kind": "arrow", "pos": [0, 0], "z": 0, "p1": [0, 0], "p2": [9, 9], "style": Style().to_dict()}
    assert document_version([plain]) == 1 and not needs_version_3(plain["style"])
    curved = {**plain, "style": Style(curve=True).to_dict()}
    assert document_version([curved]) == 3 and needs_version_3(curved["style"])
    box = {"kind": "highlight_box", "pos": [0, 0], "z": 0, "size": [9, 9], "style": Style().to_dict()}
    assert document_version([box]) == 3
    assert document_version([{"kind": "piece", "pos": [0, 0], "style": {}}, plain]) == 2
    # A rect with the usual shape and corner is still version 1: nothing changed for it
    rect = {"kind": "rect", "pos": [0, 0], "z": 0, "size": [9, 9], "style": Style(shape="rounded").to_dict()}
    assert document_version([rect]) == 1
    canvas = make_canvas(page)
    with pytest.raises(UnsupportedDocument):
        canvas.load_document({"version": 4, "items": []})
    canvas.load_document({"version": 3, "items": [curved]})
    assert isinstance(canvas.annotations()[0], ArrowItem) and canvas.annotations()[0].style.curve


# ---------------------------------------------------------------- arrows
def test_arrow_heads_at_either_end_open_or_filled_and_a_curve_with_its_own_handle(page):
    canvas = make_canvas(page)
    arrow = drag(canvas, "arrow", (20, 20), (120, 60))
    assert "control" not in arrow.handle_points()
    canvas.apply_style(heads="both", open_head=True)
    assert arrow.style.heads == "both" and arrow.boundingRect().width() > 100
    canvas.apply_style(curve=True)
    assert "control" in arrow.handle_points()
    mid = arrow.handle_points()["control"]
    assert abs(mid.x() - 70) < 1 and abs(mid.y() - 40) < 1  # starts on the line: looks straight until bent
    arrow.begin_handle_drag()
    arrow.drag_handle("control", QPointF(70, 0), False)
    assert arrow.handle_points()["control"].y() == 0
    state = arrow.get_state()
    assert state["control"] == [70.0, 0.0] and state["style"]["curve"]
    again = create_item(state, page, QRectF(page.rect()))
    assert again.handle_points()["control"] == QPointF(70, 0)
    assert again.shape().contains(QPointF(70, 20))  # the bent middle is where you click it
    assert not again.shape().contains(QPointF(70, 40))  # not where the straight line was
    canvas.apply_style(curve=False)
    assert "control" not in arrow.handle_points()  # straight again, the bend forgotten


def test_a_line_can_curve_too(page):
    canvas = make_canvas(page)
    line = drag(canvas, "line", (20, 20), (120, 20))
    canvas.apply_style(curve=True)
    line.drag_handle("control", QPointF(70, 60), False)
    assert line.boundingRect().bottom() > 40


# ---------------------------------------------------------------- shapes
def test_rectangle_shapes_and_corners(page):
    canvas = make_canvas(page)
    rect = drag(canvas, "rect", (20, 20), (120, 100))
    assert isinstance(rect, RectItem)
    plain = rect._outline().boundingRect()  # outlines are in the item's own coordinates: (0, 0) is its corner
    canvas.apply_style(shape="diamond")
    assert not rect._outline().contains(QPointF(2, 2))  # the corner is cut off
    assert rect._outline().contains(QPointF(50, 40))
    canvas.apply_style(shape="triangle")
    assert rect._outline().contains(QPointF(50, 75)) and not rect._outline().contains(QPointF(2, 2))
    canvas.apply_style(shape="star")
    assert rect._outline().contains(QPointF(50, 30)) and rect._outline().boundingRect().width() <= plain.width() + 1
    canvas.apply_style(shape="rounded", corner=30)
    assert not rect._outline().contains(QPointF(1, 1))  # a big round corner
    canvas.apply_style(corner=0)
    assert rect._outline().contains(QPointF(0.5, 0.5))  # square corners
    assert rect.get_state()["style"]["shape"] == "rounded" and rect.get_state()["style"]["corner"] == 0


# ---------------------------------------------------------------- steps
def test_step_numbering_styles_and_next_number(page):
    canvas = make_canvas(page)
    canvas.set_tool("step")
    gesture(canvas, (30, 30), (30, 30))
    gesture(canvas, (60, 30), (60, 30))
    steps = sorted((i for i in canvas.annotations() if isinstance(i, StepItem)), key=lambda i: i.number)
    assert [s.number for s in steps] == [1, 2] and canvas.next_step_number() == 3
    canvas.set_tool("select")
    canvas.select_all()
    canvas.apply_style(numbering="letters")
    assert steps[0].label() == "A" and steps[1].label() == "B"
    canvas.apply_style(numbering="roman")
    assert steps[1].label() == "II" and roman(4) == "IV" and roman(14) == "XIV" and roman(0) == "0"
    canvas.apply_style(badge="square")
    assert steps[0].style.badge == "square"
    canvas.set_tool("step")
    canvas.set_next_step_number(10)
    gesture(canvas, (90, 30), (90, 30))
    assert max(i.number for i in canvas.annotations() if isinstance(i, StepItem)) == 10
    assert canvas.next_step_number() == 11
    canvas.undo_stack.undo()
    assert canvas.next_step_number() == 10  # the number you set is next again
    canvas.set_next_step_number(None)
    assert canvas.next_step_number() == 3  # following what's on the snip


# ---------------------------------------------------------------- highlighter
def test_a_highlight_box_instead_of_a_stroke(page):
    canvas = make_canvas(page)
    canvas.apply_style(box=True)  # with Select on: nothing to change
    canvas.set_tool("highlight")
    canvas.apply_style(box=True)
    box = drag(canvas, "highlight", (20, 20), (120, 60))
    assert box.kind == "highlight_box" and box.zValue() <= -100
    state = box.get_state()
    assert state["kind"] == "highlight_box" and state["size"] == [100.0, 40.0]
    again = create_item(state, page, QRectF(page.rect()))
    assert again.kind == "highlight_box" and again.scene_rect() == QRectF(20, 20, 100, 40)
    canvas.set_tool("highlight")  # nothing selected: the tool itself changes
    canvas.apply_style(box=False)
    stroke = drag(canvas, "highlight", (20, 80), (120, 90))
    assert stroke.kind == "highlight"
    assert document_version([state]) == 3


# ---------------------------------------------------------------- text
def test_text_formatting(page):
    canvas = make_canvas(page)
    canvas.set_tool("text")
    canvas.apply_style(font_family="Serif", align="center", italic=True, underline=True, outline=True, bold=False)
    text = TextItem(canvas.tool_styles["text"], "Hello there")
    assert text.font().italic() and text.font().underline() and not text.font().bold()
    assert text.font().family() == "Serif"
    assert text.document().defaultTextOption().alignment() == Qt.AlignHCenter
    assert text.style.outline
    state = text.get_state()
    assert document_version([{**state, "pos": [0, 0]}]) == 3
    plain = TextItem(Style(), "Hi")
    assert plain.document().defaultTextOption().alignment() == Qt.AlignLeft and not plain.font().italic()


def _typed(canvas, keys):
    from PySide6.QtTest import QTest

    QTest.keyClicks(canvas.viewport(), keys)
    return canvas.editing_text_item()


def test_ctrl_z_while_typing_undoes_the_typing_with_or_without_an_outline(page):
    from PySide6.QtTest import QTest

    for outline in (False, True):
        canvas = make_canvas(page)
        canvas.set_tool("text")
        canvas.apply_style(outline=outline)
        gesture(canvas, (100, 100), (100, 100))
        text = _typed(canvas, "abc")
        assert text is not None and text.toPlainText() == "abc"
        fmt = text.document().begin().begin().fragment().charFormat()
        assert (fmt.textOutline().style() != Qt.NoPen) == outline, outline  # typed letters get the outline
        QTest.keyClick(canvas.viewport(), Qt.Key_Z, Qt.ControlModifier)
        assert text.toPlainText() == "", outline


# ---------------------------------------------------------------- redactions and spotlight
def test_rounded_redactions_and_spotlight_dimming(page):
    canvas = make_canvas(page)
    blur = drag(canvas, "blur", (20, 20), (120, 100))
    assert isinstance(blur, EffectItem) and not blur.style.rounded
    canvas.apply_style(rounded=True)
    assert blur.style.rounded and not blur.shape().contains(QPointF(0.5, 0.5))  # item coordinates
    assert blur.shape().contains(QPointF(50, 40))
    spot = drag(canvas, "spotlight", (40, 40), (100, 100))
    assert isinstance(spot, SpotlightItem) and spot.dim_alpha() == 150
    canvas.apply_style(dim=90, shape="ellipse", rounded=True)
    assert spot.dim_alpha() == round(0.9 * 255)
    assert not spot.lit_path().contains(QPointF(1, 1)) and spot.lit_path().contains(QPointF(30, 30))
    canvas.apply_style(dim=20, shape="rect", rounded=False)
    assert spot.dim_alpha() == 51 and spot.lit_path().contains(QPointF(4, 4))


# ---------------------------------------------------------------- the panel
def test_the_panel_shows_each_variant_for_its_tool(qapp):
    from snipforge.editor.panel import PropertiesPanel

    panel = PropertiesPanel()
    seen = []
    panel.changed.connect(seen.append)
    panel.load("arrow", Style(), has_selection=False)
    assert not panel.section("arrow").isHidden() and panel.section("shape").isHidden()
    shown = lambda key: not panel.switch(key).parentWidget().isHidden()
    assert shown("open_head") and shown("curve")
    assert panel.switch("box").parentWidget().isHidden()
    panel._arrow.heads.button("both").click()
    panel.switch("open_head").click()
    assert seen[-2:] == [{"heads": "both"}, {"open_head": True}]
    panel.load("rect", Style(), has_selection=False)
    assert not panel.section("shape").isHidden() and panel.section("arrow").isHidden()
    assert not panel._shapes.segment("rect").isHidden() and panel._shapes.segment("callout").isHidden()
    assert not panel._shapes.corner_slider().isHidden() and panel._shapes.corner_slider().value() == 3
    panel._shapes.segment("rect").button("star").click()
    assert seen[-1] == {"shape": "star"}
    panel.load("rect", Style(shape="star"), has_selection=False)
    assert panel._shapes._corner_row.isHidden()  # no corners on a star
    panel.load("step", Style(), has_selection=False, next_step=7)
    assert not panel.section("numbering").isHidden() and panel._steps.next.value() == 7
    nexts = []
    panel.next_step.connect(nexts.append)
    panel._steps.numbering.button("roman").click()
    panel._steps.next.setValue(12)
    assert seen[-1] == {"numbering": "roman"} and nexts == [12]
    panel.load("highlight", Style(), has_selection=False)
    assert not panel.switch("box").parentWidget().isHidden()
    panel.load("text", Style(font_family="Serif", italic=True, align="right"), has_selection=True)
    assert not panel._format.isHidden() and panel._format.toggles["italic"].isChecked()
    assert panel._format.align.value == "right" and not panel._format.toggles["bold"].isHidden()
    assert not panel.switch("outline").parentWidget().isHidden()
    panel._format.toggles["underline"].click()
    assert seen[-1] == {"underline": True}
    panel.load("callout", Style(), has_selection=False)
    assert panel._format.toggles["bold"].isHidden() and not panel._shapes.segment("callout").isHidden()
    panel.load("spotlight", Style(), has_selection=False)
    assert not panel.section("dim").isHidden() and not panel.section("shape").isHidden()
    assert not panel.switch("rounded").parentWidget().isHidden()
    panel._dim.slider.setValue(75)
    assert seen[-1] == {"dim": 75}
    panel.load("blur", Style(), has_selection=False)
    assert not panel.switch("rounded").parentWidget().isHidden() and panel.section("dim").isHidden()
    before = len(seen)
    panel.load("arrow", Style(heads="start", curve=True), has_selection=True)  # loading never echoes
    assert len(seen) == before and panel._arrow.heads.value == "start" and panel.switch("curve").isChecked()


def _ink(item, size=(420, 120), red=True):
    """The item rendered on white: its painted pixels' x range in the top rows, and the image."""
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QGraphicsScene

    scene = QGraphicsScene()
    scene.addItem(item)
    img = QImage(*size, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    painter = QPainter(img)
    scene.render(painter, QRectF(0, 0, *size), QRectF(0, 0, *size))
    painter.end()
    scene.removeItem(item)
    return img


def test_text_lines_line_up_left_center_or_right(qapp):
    def first_line(align):
        text = TextItem(Style(align=align, shadow=False, font_size=14), "a\nmuch longer line here")
        img = _ink(text, (int(text.boundingRect().width()) + 40, 120))
        xs = [x for x in range(img.width()) for y in range(8, 30) if img.pixelColor(x, y).green() < 150]
        return min(xs), max(xs)

    left, center, right = first_line("left"), first_line("center"), first_line("right")
    assert left[0] < center[0] < right[0]
    assert abs((center[0] + center[1]) / 2 - (left[0] + right[1]) / 2) < 6  # centered on the longest line


def test_callouts_center_by_default_and_can_go_left(qapp):
    from snipforge.editor.text_items import CalloutItem
    from snipforge.editor.variant_controls import TextFormatControls

    usual = CalloutItem(Style())
    assert usual.text.document().defaultTextOption().alignment() == Qt.AlignHCenter
    left = CalloutItem(Style(align="left"))
    assert left.text.document().defaultTextOption().alignment() == Qt.AlignLeft
    controls = TextFormatControls()
    controls.load("callout", Style())
    assert controls.align.value == "center"  # what you see is what's chosen
    controls.load("text", Style())
    assert controls.align.value == "left"
    assert not needs_version_3(Style().to_dict()) and needs_version_3(Style(align="left").to_dict())


def test_spotlight_and_rectangle_shapes_need_version_3(qapp):
    def state(kind, shape):
        return {"kind": kind, "pos": [0, 0], "z": 0, "size": [9, 9], "style": Style(shape=shape).to_dict()}

    assert document_version([state("spotlight", "rounded")]) == 1
    assert document_version([state("spotlight", "ellipse")]) == 3
    assert document_version([state("spotlight", "rect")]) == 3
    assert document_version([state("rect", "rect")]) == 3
    assert document_version([state("callout", "ellipse")]) == 1  # a callout shape version 1 has


def test_a_curves_bend_turns_flips_and_scales_with_the_snip(page):
    canvas = make_canvas(page)
    canvas.set_tool("line")
    canvas.apply_style(curve=True)
    line = drag(canvas, "line", (20, 20), (120, 20))
    line.drag_handle("control", QPointF(70, 100), False)
    bend = line.handle_points()["control"]
    canvas.resize_image(page.width() // 2, page.height() // 2)
    moved = canvas.annotations()[0].handle_points()["control"]
    assert abs(moved.x() - bend.x() / 2) < 1.5 and abs(moved.y() - bend.y() / 2) < 1.5
    canvas.rotate_image(clockwise=True)
    item = canvas.annotations()[0]
    points = item.handle_points()
    # Turned clockwise, the bend stays on the same side of the line: below it before, left of it now
    assert points["control"].x() < min(points["p1"].x(), points["p2"].x())


def test_an_arrows_line_never_pokes_out_past_its_head(qapp):
    for heads in ("end", "start", "both"):
        for width in (4, 12):
            arrow = ArrowItem(Style(width=width, shadow=False, heads=heads))
            arrow.set_points(QPointF(50, 60), QPointF(300, 60))
            img = _ink(arrow, (400, 120))
            red = lambda x, y, img=img: img.pixelColor(x, y).green() < 150  # noqa: E731
            if heads in ("end", "both"):
                assert not any(red(x, y) for x in range(302, 400) for y in range(120)), (heads, width)
            if heads in ("start", "both"):
                assert not any(red(x, y) for x in range(0, 48) for y in range(120)), (heads, width)


def test_the_wheel_over_the_font_and_next_number_boxes_scrolls_the_panel(qapp):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    from snipforge.editor.variant_controls import StepControls, TextFormatControls

    fonts, steps = TextFormatControls(), StepControls()
    seen = []
    fonts.changed.connect(seen.append)
    steps.next.setValue(3)
    for box in (fonts.font, steps.next):
        pos = QPointF(box.rect().center())
        QApplication.sendEvent(box, QWheelEvent(pos, box.mapToGlobal(pos), QPoint(0, 0), QPoint(0, -120),
                                                Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))
    assert seen == [] and steps.next.value() == 3
