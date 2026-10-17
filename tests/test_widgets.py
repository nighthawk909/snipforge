"""Shared controls: toggle switch and segmented control."""
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from snipforge import theme
from snipforge.widgets import Segmented, ToggleSwitch


def click(widget, pos=None):
    pos = QPointF(pos or widget.rect().center())
    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        QApplication.sendEvent(widget, QMouseEvent(kind, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))


def test_switch_toggles_on_click_and_space(qapp):
    sw = ToggleSwitch("Shadow")
    seen = []
    sw.toggled.connect(seen.append)
    sw.resize(sw.sizeHint())
    click(sw)
    assert sw.isChecked() and seen == [True]
    sw.setFocus()
    for kind in (QEvent.KeyPress, QEvent.KeyRelease):
        QApplication.sendEvent(sw, QKeyEvent(kind, Qt.Key_Space, Qt.NoModifier, " "))
    assert not sw.isChecked() and seen == [True, False]
    assert sw.accessibleName() == "Shadow"


def test_switch_paints_accent_when_on(qapp):
    sw = ToggleSwitch("Shadow")
    sw.resize(sw.sizeHint())
    sw.setChecked(True)
    img = QImage(sw.size(), QImage.Format_ARGB32)
    img.fill(0)
    sw.render(img)
    accent = theme.current().accent.fill.lower()
    track = img.pixelColor(6, sw.height() // 2).name()
    assert track == accent


def test_segmented_selects_one_option_and_reports_changes(qapp):
    seg = Segmented([("stable", "Stable"), ("preview", "Preview")], value="stable")
    seen = []
    seg.changed.connect(seen.append)
    assert seg.value == "stable"
    click(seg.button("preview"))
    assert seg.value == "preview" and seen == ["preview"]
    seg.set_value("stable")  # programmatic change: no signal, like loading saved settings
    assert seg.value == "stable" and seen == ["preview"]
    assert seg.button("stable").isChecked() and not seg.button("preview").isChecked()


def test_segmented_rejects_unknown_values(qapp):
    seg = Segmented([("a", "A"), ("b", "B")], value="a")
    try:
        seg.set_value("zzz")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
    assert seg.value == "a"
