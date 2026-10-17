"""Beautify: the editor previews the backdrop, corners and shadow it adds, and every switch agrees."""
import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QApplication

from snipforge.editor import beautify
from snipforge.editor.beautify import BeautifyStyle, apply_beautify
from snipforge.editor.window import EditorWindow
from snipforge.library import Library
from snipforge.settings import app_settings

STYLE = BeautifyStyle()


@pytest.fixture(autouse=True)
def beautify_off():
    app_settings().remove("beautify")
    yield
    app_settings().remove("beautify")


@pytest.fixture
def make_editor(qapp, tmp_path, white_image):
    made = []

    def build() -> EditorWindow:
        lib = Library(tmp_path)
        w = EditorWindow(white_image, lib, lib.create(white_image))
        made.append(w)
        w.resize(1100, 700)
        w.show()
        QApplication.processEvents()
        return w

    yield build
    for w in made:
        w.close()


def _on_screen(canvas, scene_point: QPointF) -> QColor:
    QApplication.processEvents()
    shot = canvas.viewport().grab().toImage()
    pos = canvas.mapFromScene(scene_point)
    return shot.pixelColor(round(pos.x() * shot.devicePixelRatio()), round(pos.y() * shot.devicePixelRatio()))


def _is_backdrop(color: QColor) -> bool:
    """Somewhere on the purple-to-teal gradient (possibly darkened by the shadow), not the gray canvas."""
    return color.saturation() > 60 and color.blue() > color.red()


def test_the_editor_shows_the_backdrop_only_while_beautify_is_on(make_editor):
    editor = make_editor()
    canvas = editor.canvas
    beside = QPointF(-STYLE.padding / 2, 50)  # left of the snip, inside the padding
    corner = QPointF(1, 1)  # cut off by the rounded corner
    middle = QPointF(100, 50)
    assert not _is_backdrop(_on_screen(canvas, beside))
    assert _on_screen(canvas, corner).lightness() > 240  # the white snip, square corners
    editor.tray.beautify.click()
    assert _is_backdrop(_on_screen(canvas, beside))
    assert _is_backdrop(_on_screen(canvas, corner))
    assert _on_screen(canvas, middle).lightness() > 240  # the snip itself is untouched
    editor.tray.beautify.click()
    assert not _is_backdrop(_on_screen(canvas, beside))


def test_fit_shows_the_whole_backdrop(make_editor):
    editor = make_editor()
    editor.tray.beautify.click()
    canvas = editor.canvas
    canvas.set_zoom(4.0)
    canvas.fit_image()
    QApplication.processEvents()
    frame = canvas.frame_rect()
    assert frame == QRectF(canvas.crop_rect()).adjusted(-STYLE.padding, -STYLE.padding, STYLE.padding, STYLE.padding)
    shown = canvas.mapToScene(canvas.viewport().rect()).boundingRect()
    assert shown.contains(frame)


def test_preview_is_never_exported(make_editor, white_image):
    editor = make_editor()
    editor.tray.beautify.click()
    plain = editor.canvas.render_image()
    assert plain.size() == white_image.size() and plain.pixelColor(1, 1).lightness() > 240
    assert editor.final_image().size() == apply_beautify(plain).size()
    editor.copy_to_clipboard()
    assert QGuiApplication.clipboard().image().size() == apply_beautify(plain).size()
    assert editor.statusBar().currentMessage() == "Copied to clipboard with Beautify"


def test_no_preview_while_cropping(make_editor):
    editor = make_editor()
    editor.tray.beautify.click()
    canvas = editor.canvas
    canvas.set_tool("crop")
    assert not _is_backdrop(_on_screen(canvas, QPointF(-STYLE.padding / 2, 50)))
    canvas.set_tool("select")
    assert _is_backdrop(_on_screen(canvas, QPointF(-STYLE.padding / 2, 50)))


def test_every_switch_and_open_editor_agree(make_editor, tmp_path):
    from snipforge.settings_window import SettingsWindow
    first, second = make_editor(), make_editor()
    settings = SettingsWindow(library_root=tmp_path)
    try:
        first.tray.beautify.click()
        assert beautify.enabled() and app_settings().value("beautify", False, bool)
        assert second.tray.beautify.isChecked() and settings.beautify.isChecked()
        assert _is_backdrop(_on_screen(second.canvas, QPointF(-STYLE.padding / 2, 50)))
        assert second.final_image().size() != second.canvas.render_image().size()
        settings.beautify.click()  # off again from Settings › Saving
        assert not beautify.enabled()
        assert not first.tray.beautify.isChecked() and not second.tray.beautify.isChecked()
        assert first.final_image().size() == first.canvas.render_image().size()
    finally:
        settings.close()


def test_a_new_editor_starts_with_the_saved_choice(make_editor):
    beautify.set_enabled(True)
    editor = make_editor()
    assert editor.tray.beautify.isChecked()
    assert _is_backdrop(_on_screen(editor.canvas, QPointF(-STYLE.padding / 2, 50)))


def test_closed_editors_stop_listening(qapp, tmp_path, white_image):
    from PySide6.QtCore import QEvent, Qt
    lib = Library(tmp_path)
    editor = EditorWindow(white_image, lib, None)
    editor.setAttribute(Qt.WA_DeleteOnClose)  # as the app opens them
    editor.show()
    editor.close()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    beautify.set_enabled(True)  # nothing may touch the deleted window
    beautify.set_enabled(False)
