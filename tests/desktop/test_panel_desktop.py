"""The editor's properties panel with Windows' real fonts and SnipForge's theme: every tool's
controls fit across the panel, so it never needs a sideways scroll bar.

    QT_QPA_PLATFORM=windows python -m pytest -m desktop tests/desktop/test_panel_desktop.py

Offscreen tests can't check this: the offscreen platform's stand-in font is far wider than
Segoe UI, so widths measured there say nothing about what you see.
"""
import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from snipforge import theme
from snipforge.editor.panel import PANEL_WIDTH, PropertiesPanel
from snipforge.editor.style import DEFAULT_TOOL_STYLES

pytestmark = pytest.mark.desktop


@pytest.fixture(scope="module")
def themed(qapp):
    if QGuiApplication.platformName() == "offscreen":
        pytest.skip("desktop tests need a real display: set QT_QPA_PLATFORM=windows")
    sheet, palette = qapp.styleSheet(), qapp.palette()
    theme.install(qapp)
    yield qapp
    qapp.setStyleSheet(sheet)
    qapp.setPalette(palette)


@pytest.mark.parametrize("kind", sorted(DEFAULT_TOOL_STYLES))
def test_every_tools_controls_fit_across_the_panel(themed, kind):
    panel = PropertiesPanel()
    panel.resize(PANEL_WIDTH, 360)  # short, so the vertical scroll bar takes its share of the width
    panel.show()
    try:
        panel.load(kind, DEFAULT_TOOL_STYLES[kind], has_selection=True)
        QApplication.processEvents()
        room = panel.viewport().width()
        if not panel.verticalScrollBar().isVisible():  # a short tool: it would need the room in a smaller window
            room -= panel.verticalScrollBar().sizeHint().width()
        needed = panel.widget().minimumSizeHint().width()
        assert needed <= room, f"{kind}: needs {needed} px, has {room}"
        assert not panel.horizontalScrollBar().isVisible()
    finally:
        panel.close()
        panel.deleteLater()
