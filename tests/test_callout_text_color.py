"""A callout's text color: automatic (dark or white, whichever reads on the fill) unless you pick one."""
import json

from PySide6.QtGui import QColor

from snipforge.editor import saved_styles
from snipforge.editor.panel import PropertiesPanel
from snipforge.editor.style import DEFAULT_TOOL_STYLES, STYLE_FIELDS, Style
from snipforge.editor.text_items import CalloutItem
from tests.test_editor import drag, make_canvas


def text_color(callout: CalloutItem) -> str:
    return callout.text.defaultTextColor().name()


def test_automatic_by_default_and_your_color_when_picked(qapp):
    callout = CalloutItem(DEFAULT_TOOL_STYLES["callout"].with_changes(fill_color="#ffd60a"))
    assert text_color(callout) == "#1c1c1e"  # dark reads on yellow
    callout.set_style(callout.style.with_changes(text_color="#bf5af2"))
    assert text_color(callout) == "#bf5af2"
    callout.set_style(callout.style.with_changes(text_color=""))
    assert text_color(callout) == "#1c1c1e"  # back to automatic


def test_it_is_kept_with_the_snip_and_damaged_values_are_ignored(qapp):
    style = Style(text_color="#34c759")
    assert Style.from_dict(json.loads(json.dumps(style.to_dict()))).text_color == "#34c759"
    assert Style.from_dict({"text_color": "not a color"}).text_color == ""
    assert Style.from_dict({"text_color": 7}).text_color == ""


def test_callouts_have_it_and_saved_quick_styles_keep_it(qapp):
    assert "text_color" in STYLE_FIELDS["callout"]
    assert "text_color" not in STYLE_FIELDS["text"]  # a text box's Color is its text color already
    assert saved_styles.fields_of("callout", Style(text_color="#ff3b30"))["text_color"] == "#ff3b30"


def test_the_panel_shows_text_color_for_callouts_only(qapp):
    panel = PropertiesPanel()
    panel.load("callout", DEFAULT_TOOL_STYLES["callout"], has_selection=True)
    controls = panel.text_color_controls()
    assert not controls.isHidden()
    assert controls.automatic.isChecked()
    panel.load("text", DEFAULT_TOOL_STYLES["text"], has_selection=True)
    assert controls.isHidden()


def test_picking_a_text_color_in_the_panel(qapp):
    panel = PropertiesPanel()
    panel.load("callout", DEFAULT_TOOL_STYLES["callout"], has_selection=True)
    seen = []
    panel.changed.connect(seen.append)
    controls = panel.text_color_controls()
    controls.swatches["#ffffff"].click()
    assert seen == [{"text_color": "#ffffff"}]
    panel.load("callout", DEFAULT_TOOL_STYLES["callout"].with_changes(text_color="#ffffff"), has_selection=True)
    assert controls.swatches["#ffffff"].isChecked() and not controls.automatic.isChecked()
    controls.automatic.click()
    assert seen[-1] == {"text_color": ""}


def test_text_color_in_the_editor_applies_to_the_selected_callout(white_image):
    canvas = make_canvas(white_image)
    callout = drag(canvas, "callout", (20, 20), (150, 80))
    canvas.commit_text()
    canvas._select([callout])
    canvas.apply_style(text_color="#34c759")
    assert QColor(text_color(callout)) == QColor("#34c759")
    canvas.undo_stack.undo()
    assert callout.style.text_color == ""
