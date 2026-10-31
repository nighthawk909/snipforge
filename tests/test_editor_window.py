"""Editor window wiring: focus, the tray, Copy, save state and panel behavior."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication, QSlider, QSpinBox, QWidget

from snipforge.editor.style import Style
from snipforge.editor.window import EditorWindow
from snipforge.library import Library
from snipforge.settings import app_settings
from snipforge.widgets import ToggleSwitch
from tests.test_editor import gesture


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


def ui_words(widget: QWidget) -> str:
    """Every label, button text, accessible name and tooltip in `widget`, lower case."""
    found = []
    for w in (widget, *widget.findChildren(QWidget)):
        text = getattr(w, "text", None)
        found += [text() if callable(text) else "", w.accessibleName(), w.toolTip()]
    return " ".join(t for t in found if isinstance(t, str)).lower()


def test_the_bottom_bar_has_no_beautify_switch_and_closes_up(editor):
    tray = editor.tray
    assert not tray.findChildren(ToggleSwitch) and "beautif" not in ui_words(tray)
    assert not hasattr(tray, "beautify") and not hasattr(tray, "beautify_changed")
    gap = tray._size.geometry().left() - tray.status.geometry().right() - 1
    assert gap == tray.layout().spacing()  # the image size sits right after the messages: no hole


def test_no_beautify_command_in_the_command_palette(editor):
    words = [" ".join(c.words()) for c in editor.commands_for_palette()]
    assert words and not [w for w in words if "beautif" in w]


@pytest.fixture
def marked(qapp):
    """200x100 white with a red square at 40,30-60,50."""
    image = QImage(200, 100, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    for x in range(40, 60):
        for y in range(30, 50):
            image.setPixelColor(x, y, QColor("#ff0000"))
    return image


def test_copy_hands_on_exactly_the_cropped_snip(qapp, tmp_path, marked):
    app_settings().setValue("beautify", True)  # left by a SnipForge that had Beautify: it's never read
    lib = Library(tmp_path)
    w = EditorWindow(marked, lib, lib.create(marked))
    try:
        w.resize(1100, 700)
        w.show()
        QApplication.processEvents()
        w.canvas.set_tool("crop")
        gesture(w.canvas, (30, 20), (130, 80))  # a real drag with the Crop tool
        w.canvas.set_tool("select")
        assert w.canvas.crop_rect() == QRectF(30, 20, 100, 60)
        w.copy_to_clipboard()
        copied = QGuiApplication.clipboard().image().convertToFormat(QImage.Format_ARGB32)
        rendered = w.canvas.render_image().convertToFormat(QImage.Format_ARGB32)
        assert copied.size().toTuple() == (100, 60) and copied == rendered  # nothing added around it
        assert copied.pixelColor(10, 10).name() == "#ff0000" and copied.pixelColor(0, 0).name() == "#ffffff"
        assert w.final_image().size().toTuple() == (100, 60)
        assert w.statusBar().currentMessage() == "Copied to clipboard"
    finally:
        w.close()
        app_settings().remove("beautify")


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
