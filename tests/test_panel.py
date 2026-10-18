"""The editor's properties panel: what it shows per tool and what it emits."""
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from snipforge.editor.panel import PropertiesPanel
from snipforge.editor.quick_styles import QUICK_STYLES, changes_for
from snipforge.editor.style import DEFAULT_TOOL_STYLES, STYLE_FIELDS, Style


def click(widget):
    pos = QPointF(widget.rect().center())
    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        QApplication.sendEvent(widget, QMouseEvent(kind, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))


def visible(panel, name):
    return not panel.section(name).isHidden()


def test_quick_style_changes_only_touch_fields_the_tool_has():
    solid_red = next(q for q in QUICK_STYLES if q.fill_on)
    assert changes_for("arrow", solid_red) == {"color": solid_red.color}
    assert changes_for("callout", solid_red) == {"color": solid_red.color, "fill": True}
    assert changes_for("blur", solid_red) == {}
    for q in QUICK_STYLES:
        for kind, fields in STYLE_FIELDS.items():
            assert set(changes_for(kind, q)) <= set(fields)


def test_sections_follow_the_tool(qapp):
    panel = PropertiesPanel()
    panel.load("callout", DEFAULT_TOOL_STYLES["callout"], has_selection=True)
    assert panel.title() == "Callout" and panel.chip_visible()
    for name in ("quick", "shape", "color", "size", "text", "effects"):
        assert visible(panel, name), name
    assert not visible(panel, "crop") and not visible(panel, "image") and not visible(panel, "stamp")
    assert panel.size_label() == "Border"

    panel.load("blur", DEFAULT_TOOL_STYLES["blur"], has_selection=False)
    assert visible(panel, "size") and panel.size_label() == "Strength"
    assert not visible(panel, "color") and not panel.chip_visible()
    assert visible(panel, "quick") and panel.quick_tile(0).isHidden()  # only your saved styles for blur

    panel.load("crop", None, has_selection=False)
    assert visible(panel, "crop") and not visible(panel, "size")

    panel.load("select", None, has_selection=False)
    assert visible(panel, "image") and not visible(panel, "effects")

    panel.load("stamp", DEFAULT_TOOL_STYLES["stamp"], has_selection=False)
    assert visible(panel, "stamp") and not visible(panel, "color")


def test_controls_emit_style_changes(qapp):
    panel = PropertiesPanel()
    panel.load("rect", Style(color="#ff3b30", width=4, fill=False, dash=False, shadow=True), has_selection=True)
    seen = []
    panel.changed.connect(seen.append)
    panel.switch("dash").click()
    panel.switch("shadow").click()
    panel.slider().setValue(9)
    click(panel.swatch("#34c759"))
    assert {"dash": True} in seen and {"shadow": False} in seen
    assert {"width": 9.0} in seen and {"color": "#34c759"} in seen


def test_loading_a_style_does_not_echo_changes(qapp):
    panel = PropertiesPanel()
    seen = []
    panel.changed.connect(seen.append)
    panel.load("callout", Style(color="#0a84ff", width=6, fill=True, tail=False), has_selection=True)
    assert seen == []
    assert panel.switch("tail").isChecked() is False and panel.slider().value() == 6
    assert panel.swatch("#0a84ff").isChecked()


def test_quick_style_tile_applies_its_changes(qapp):
    panel = PropertiesPanel()
    panel.load("callout", DEFAULT_TOOL_STYLES["callout"], has_selection=True)
    seen = []
    panel.changed.connect(seen.append)
    click(panel.quick_tile(0))
    assert seen == [changes_for("callout", QUICK_STYLES[0])]


def test_crop_buttons_and_stamp_choice(qapp):
    panel = PropertiesPanel()
    actions, stamps = [], []
    panel.crop_action.connect(actions.append)
    panel.changed.connect(stamps.append)
    panel.load("crop", None, has_selection=False)
    for name in ("apply", "cancel", "reset"):
        click(panel.crop_button(name))
    assert actions == ["apply", "cancel", "reset"]
    panel.load("stamp", DEFAULT_TOOL_STYLES["stamp"], has_selection=False)
    click(panel.stamp_button("star"))
    assert stamps == [{"stamp": "star"}]


def test_image_section_reports_size(qapp):
    panel = PropertiesPanel()
    panel.load("select", None, has_selection=False)
    panel.set_image_size(1280, 720)
    assert "1280 × 720" in panel.image_size_text()


def test_tray_beautify_switch_reports_changes_but_not_loading(qapp):
    from snipforge.editor.chrome import Tray
    tray = Tray()
    seen = []
    tray.beautify_changed.connect(seen.append)
    tray.set_beautify(True)  # loading the saved value doesn't emit
    assert seen == [] and tray.beautify.isChecked()
    tray.beautify.click()
    assert seen == [False]
