"""Editor window wiring: focus, the tray, Beautify, save state and panel behavior."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication, QSlider, QSpinBox, QWidget

from snipforge.editor.style import Style
from snipforge.editor.window import EditorWindow
from snipforge.library import Library
from snipforge.settings import app_settings


@pytest.fixture
def editor(qapp, tmp_path, white_image):
    lib = Library(tmp_path)
    for _ in range(10):
        lib.create(white_image)
    w = EditorWindow(white_image, lib, lib.create(white_image), on_new_snip=lambda: None)
    w.resize(1100, 700)
    w.show()
    w.activateWindow()
    QApplication.processEvents()
    yield w
    w.close()
    app_settings().remove("beautify")


def test_clicking_panel_and_bar_controls_keeps_focus_on_the_canvas(editor):
    editor.canvas.setFocus()
    QApplication.processEvents()
    for widget in (editor.panel.swatch("#34c759"), editor.panel.switch("shadow"), editor.commands.copy_button):
        QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, widget.rect().center())
        QApplication.processEvents()
        assert QApplication.focusWidget() is editor.canvas, type(QApplication.focusWidget()).__name__
    assert editor.panel.focusPolicy() == Qt.NoFocus
    assert editor.panel.viewport().focusPolicy() == Qt.NoFocus


def test_controls_are_reachable_with_tab_but_not_focused_by_clicks(editor):
    buttons = [w for w in (*editor.panel.findChildren(QAbstractButton), *editor.commands.findChildren(QAbstractButton),
                           *editor.tray.findChildren(QAbstractButton)) if w.isVisibleTo(editor)]
    assert buttons and all(b.focusPolicy() == Qt.TabFocus for b in buttons), \
        [b.accessibleName() or b.text() for b in buttons if b.focusPolicy() != Qt.TabFocus]
    font = editor.panel.findChild(QSpinBox)
    assert font.focusPolicy() == Qt.StrongFocus  # you can type a font size
    assert editor.panel.slider().accessibleName()


def test_status_messages_have_room_next_to_ten_recent_snips(editor):
    assert editor.tray.thumb_count() == 10
    editor.copy_to_clipboard()
    assert editor.statusBar() is editor.tray.status
    assert editor.statusBar().currentMessage() == "Copied to clipboard"
    assert editor.tray.status.width() >= 220


def test_beautify_is_always_visible_and_persists(editor, white_image):
    toggle = editor.tray.beautify
    for tool in ("select", "rect", "callout"):
        editor.canvas.set_tool(tool)
        assert toggle.isVisibleTo(editor)
    toggle.click()
    assert app_settings().value("beautify", False, bool) is True
    assert editor.final_image().size() != white_image.size()
    toggle.click()
    assert editor.final_image().size() == white_image.size()


def test_undo_buttons_follow_the_history(editor):
    assert not editor.commands.undo_button.isEnabled()
    editor.canvas.set_tool("rect")
    editor.canvas._begin_create(QPointF(10, 10))
    editor.canvas._update_create(editor.canvas._drag, QPointF(80, 60), False)
    editor.canvas._finish_create(editor.canvas._drag)
    editor.canvas._drag = None
    assert editor.commands.undo_button.isEnabled()
    editor.canvas.undo_stack.undo()
    assert not editor.commands.undo_button.isEnabled() and editor.commands.redo_button.isEnabled()


def test_save_state_tracks_autosave(editor, monkeypatch):
    editor.canvas.set_tool("rect")
    editor.canvas._begin_create(QPointF(10, 10))
    editor.canvas._update_create(editor.canvas._drag, QPointF(80, 60), False)
    editor.canvas._finish_create(editor.canvas._drag)
    editor.canvas._drag = None
    assert editor.tray.saved_text() == "Saving…"
    editor._save_to_library()
    assert editor.tray.saved_text().startswith("Saved")

    def broken(*_a):
        raise OSError("disk full")

    monkeypatch.setattr(editor._library, "save", broken)
    editor._save_to_library()
    assert editor.tray.saved_text() == "Not saved"


def test_custom_color_clears_the_swatch_ring(qapp):
    from snipforge.editor.panel import PropertiesPanel
    panel = PropertiesPanel()
    panel.load("rect", Style(color="#ff3b30"), has_selection=True)
    panel.load("rect", Style(color="#123456"), has_selection=True)
    assert [s.color for s in panel._swatches if s.isChecked()] == []


def test_wheel_over_the_size_slider_scrolls_the_panel_instead(qapp):
    from snipforge.editor.panel import PropertiesPanel
    panel = PropertiesPanel()
    panel.load("rect", Style(width=4), has_selection=True)
    seen = []
    panel.changed.connect(seen.append)
    slider: QSlider = panel.slider()
    pos = QPointF(slider.rect().center())
    wheel = QWheelEvent(pos, slider.mapToGlobal(pos), QPoint(0, 0), QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                        Qt.NoScrollPhase, False)
    QApplication.sendEvent(slider, wheel)
    assert slider.value() == 4 and seen == []


def test_panel_content_fits_its_width_for_every_tool(qapp):
    from snipforge.editor.panel import PANEL_WIDTH, PropertiesPanel
    from snipforge.editor.style import DEFAULT_TOOL_STYLES
    panel = PropertiesPanel()
    for kind in ("select", "crop", *DEFAULT_TOOL_STYLES):
        panel.load(kind, DEFAULT_TOOL_STYLES.get(kind), has_selection=False)
        body: QWidget = panel.widget()
        assert body.minimumSizeHint().width() <= PANEL_WIDTH - 14, kind  # leaves room for a scrollbar
    assert panel.horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded


def test_command_bar_drops_labels_when_narrow(editor):
    editor.resize(900, 700)
    QApplication.processEvents()
    assert editor.commands.copy_button.text() == "Copy"  # the main action keeps its label
    labels = [b.text() for b in editor.commands.findChildren(QAbstractButton) if b.isVisibleTo(editor)]
    assert "Grab text" not in labels
    editor.resize(1500, 700)
    QApplication.processEvents()
    labels = [b.text() for b in editor.commands.findChildren(QAbstractButton) if b.isVisibleTo(editor)]
    assert "Grab text" in labels


def test_dot_grid_survives_a_crop(editor):
    from PySide6.QtCore import QRectF
    editor.canvas.set_crop_rect(QRectF(20, 10, 100, 60))
    editor.canvas.apply_crop()
    QApplication.processEvents()
    image = editor.canvas.viewport().grab().toImage()
    colors = {image.pixelColor(x, y).name() for x in range(0, 60) for y in range(0, 40)}
    assert len(colors) > 1  # dots, not one flat fill


def test_event_filter_constants_are_shared():
    from snipforge.editor import canvas, window
    assert window.MIN_ZOOM == canvas.MIN_ZOOM and window.MAX_ZOOM == canvas.MAX_ZOOM


_ = QEvent  # keep the import for readers adding event-based tests
