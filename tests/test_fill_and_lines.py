"""A fill color of its own and a fill opacity for shapes, callouts and text, and dotted lines."""
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor

from snipforge.editor.panel import PropertiesPanel
from snipforge.editor.quick_styles import QUICK_STYLES, changes_for
from snipforge.editor.quick_tiles import describe
from snipforge.editor.style import DEFAULT_TOOL_STYLES, USUAL_OPACITY, Style, fill_opacity
from tests.test_editor import drag, make_canvas
from tests.test_panel import click

BLUE, RED = "#0a84ff", "#ff3b30"


def pixel(canvas, x, y) -> QColor:
    return canvas.render_image().pixelColor(x, y)


def close(a: QColor, b: QColor, tolerance=6) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a.getRgb()[:3], b.getRgb()[:3]))


def over_white(color: str, alpha: float) -> QColor:
    c = QColor(color)
    return QColor(*(round(v * alpha + 255 * (1 - alpha)) for v in (c.red(), c.green(), c.blue())))


# --- the style ------------------------------------------------------------------------------
def test_new_fields_survive_saving_and_damaged_values_fall_back():
    style = Style(fill=True, fill_color=BLUE, fill_opacity=40, dash=True, dotted=True)
    assert Style.from_dict(style.to_dict()) == style
    damaged = Style.from_dict({"fill_color": "not a color", "fill_opacity": "x", "dotted": "yes"})
    assert (damaged.fill_color, damaged.fill_opacity, damaged.dotted) == ("", USUAL_OPACITY, False)
    assert Style.from_dict({"fill_opacity": 250}).fill_opacity == 100
    assert Style.from_dict({"fill_opacity": -7}).fill_opacity == USUAL_OPACITY


def test_snips_saved_before_look_the_same(qapp, white_image):
    """Older documents have no fill color or opacity: a filled shape is still a light tint of its color,
    and a filled callout is still solid."""
    canvas = make_canvas(white_image)
    drag(canvas, "rect", (20, 20), (100, 80))
    canvas.apply_style(fill=True, color=RED, shadow=False)  # a shadow shows through a see-through fill
    assert close(pixel(canvas, 60, 50), over_white(RED, 70 / 255))
    assert fill_opacity("rect", canvas.tool_styles["rect"]) == 27 and fill_opacity("callout", Style()) == 100


def test_a_shape_fills_with_its_own_color_at_its_opacity(qapp, white_image):
    canvas = make_canvas(white_image)
    drag(canvas, "rect", (20, 20), (100, 80))
    canvas.apply_style(fill=True, color=RED, fill_color=BLUE, fill_opacity=50, shadow=False)
    assert close(pixel(canvas, 60, 50), over_white(BLUE, 0.5))
    assert close(pixel(canvas, 20, 50), QColor(RED), tolerance=40)  # the outline keeps its color


def test_a_callout_with_its_own_fill_keeps_its_outline_color_and_readable_text(qapp, white_image):
    canvas = make_canvas(white_image)
    item = drag(canvas, "callout", (20, 10), (180, 70))
    canvas.commit_text()
    canvas.apply_style(color=RED, fill=True, fill_color="#ffd60a", fill_opacity=100)
    assert close(pixel(canvas, 40, 40), QColor("#ffd60a"))
    assert item.text.defaultTextColor() == QColor("#1c1c1e")  # dark text reads on yellow
    canvas.apply_style(fill_opacity=20)  # a faint fill: the text takes the outline's color
    assert item.text.defaultTextColor() == QColor(RED)


def test_text_with_a_background_color_of_its_own_uses_its_color_for_the_letters(qapp, white_image):
    from snipforge.editor.text_items import TextItem
    usual = TextItem(Style(color=RED, fill=True))
    assert usual.defaultTextColor() == QColor("#ffffff")  # the usual look: white on the red box
    own = TextItem(Style(color=RED, fill=True, fill_color="#ffd60a"))
    assert own.defaultTextColor() == QColor(RED)


def test_dotted_lines_have_round_gaps(qapp, white_image):
    canvas = make_canvas(white_image)
    drag(canvas, "line", (10, 50), (190, 50))
    canvas.apply_style(width=6.0, shadow=False, dash=True, dotted=True)
    row = [pixel(canvas, x, 50) for x in range(30, 170)]
    inked = [c.lightness() < 200 for c in row]
    assert any(inked) and not all(inked)  # dots with gaps between
    canvas.apply_style(dash=False, dotted=False)
    assert all(c.lightness() < 200 for c in (pixel(canvas, x, 50) for x in range(30, 170)))


def test_a_quick_style_brings_back_the_usual_fill():
    red = next(q for q in QUICK_STYLES if q.fill_on)
    assert changes_for("rect", red)["fill_color"] == "" and changes_for("rect", red)["fill_opacity"] == USUAL_OPACITY
    assert "fill_color" not in changes_for("arrow", red)


def test_saved_styles_say_how_they_differ():
    style = Style(color=RED, fill=True, fill_color=BLUE, fill_opacity=40, dash=True, dotted=True)
    assert describe("rect", style) == "Red, 4 px, filled Blue 40%, dotted, shadow"


# --- the panel ------------------------------------------------------------------------------
def visible(panel, name):
    return not panel.section(name).isHidden()


def test_the_fill_section_shows_while_fill_is_on(qapp):
    panel = PropertiesPanel()
    panel.load("rect", Style(fill=False), has_selection=True)
    assert not visible(panel, "fill")
    panel.load("rect", Style(fill=True), has_selection=True)
    assert visible(panel, "fill")
    panel.load("arrow", Style(fill=True), has_selection=True)
    assert not visible(panel, "fill")  # lines have no inside
    panel.load("text", DEFAULT_TOOL_STYLES["text"], has_selection=True)
    assert visible(panel, "fill") and panel.fill_controls().title.text() == "BACKGROUND"


def test_fill_controls_emit_their_changes(qapp):
    panel = PropertiesPanel()
    panel.load("rect", Style(fill=True, color=RED), has_selection=True)
    fill = panel.fill_controls()
    assert fill.same.isChecked() and fill.opacity.value() == 27
    seen = []
    panel.changed.connect(seen.append)
    click(fill.swatches[BLUE])
    fill.opacity.setValue(60)
    click(fill.same)
    assert seen == [{"fill_color": BLUE}, {"fill_opacity": 60}, {"fill_color": ""}]


def test_the_line_control_offers_solid_dashed_and_dotted(qapp):
    panel = PropertiesPanel()
    panel.load("arrow", Style(dash=True, dotted=True), has_selection=True)
    assert panel.line_control().value == "dotted"
    seen = []
    panel.changed.connect(seen.append)
    panel.line_control().button("solid").click()
    panel.line_control().button("dotted").click()
    assert seen == [{"dash": False, "dotted": False}, {"dash": True, "dotted": True}]


@pytest.mark.parametrize("kind", ["rect", "ellipse", "callout", "text"])
def test_fill_changes_undo_in_one_step_per_drag(qapp, white_image, kind):
    canvas = make_canvas(white_image)
    item = drag(canvas, kind, (30, 20), (130, 70)) if kind != "text" else None
    if item is None:
        from snipforge.editor.text_items import TextItem
        item = canvas._adopt(TextItem(canvas.tool_styles["text"], "Note"))
        item.setPos(QPointF(40, 40))
        canvas._scene.addItem(item)
    canvas.commit_text()
    canvas._select([item])
    canvas.apply_style(fill=True)
    steps = canvas.undo_stack.count()
    for value in (90, 70, 50):  # a slider drag
        canvas.apply_style(fill_opacity=value)
    assert canvas.undo_stack.count() == steps + 1 and item.style.fill_opacity == 50
    canvas.undo_stack.undo()
    assert item.style.fill_opacity == USUAL_OPACITY
