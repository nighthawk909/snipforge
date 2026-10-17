"""Beautify: the editor previews the backdrop, corners and shadow it adds, and every switch agrees."""
import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QGuiApplication, QImage
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


def _listeners() -> int:
    from PySide6.QtCore import SIGNAL
    return beautify.switch().receivers(SIGNAL("changed(bool)"))


def test_closed_editors_stop_listening(qapp, tmp_path, white_image):
    import shiboken6
    from PySide6.QtCore import QEvent, Qt
    before = _listeners()
    lib = Library(tmp_path)
    deleted = EditorWindow(white_image, lib, None)
    deleted.setAttribute(Qt.WA_DeleteOnClose)  # as the app opens them
    kept = EditorWindow(white_image, lib, None)  # e.g. the self-test's
    assert _listeners() == before + 2
    for w in (deleted, kept):
        w.show()
        w.close()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not shiboken6.isValid(deleted)
    assert _listeners() == before
    beautify.set_enabled(True)
    assert not kept.tray.beautify.isChecked()  # closed: it no longer follows the switch


def test_preview_matches_the_export_pixel_for_pixel(make_editor):
    editor = make_editor()
    canvas = editor.canvas
    editor.tray.beautify.click()
    canvas.set_zoom(1.0)
    canvas.centerOn(canvas.frame_rect().center())
    QApplication.processEvents()
    export = editor.final_image().convertToFormat(QImage.Format_ARGB32)
    shot = canvas.viewport().grab().toImage().convertToFormat(QImage.Format_ARGB32)
    origin = canvas.mapFromScene(canvas.frame_rect().topLeft())
    assert canvas.viewport().rect().contains(canvas.mapFromScene(canvas.frame_rect()).boundingRect())
    far = 0
    for y in range(export.height()):
        for x in range(export.width()):
            a, b = export.pixelColor(x, y), shot.pixelColor(origin.x() + x, origin.y() + y)
            if max(abs(a.red() - b.red()), abs(a.green() - b.green()), abs(a.blue() - b.blue())) > 16:
                far += 1
    assert far < export.width() * export.height() * 0.005, far  # only antialiased corner pixels may differ


def test_switch_never_gets_out_of_step_with_copy(make_editor):
    editor = make_editor()
    app_settings().setValue("beautify", True)  # changed behind the editor's back
    editor.tray.beautify.click()  # shows ON: so must the preview and Copy
    assert editor.tray.beautify.isChecked()
    assert editor.final_image().size() != editor.canvas.render_image().size()
    assert _is_backdrop(_on_screen(editor.canvas, QPointF(-STYLE.padding / 2, 50)))
    editor.tray.beautify.click()
    assert not editor.tray.beautify.isChecked()
    assert editor.final_image().size() == editor.canvas.render_image().size()


def test_preview_follows_crop_apply_and_undo(make_editor):
    from snipforge.editor.commands import CropCommand
    editor = make_editor()
    canvas = editor.canvas
    editor.tray.beautify.click()
    full = QRectF(canvas.crop_rect())
    crop = QRectF(40, 20, 100, 60)
    canvas.undo_stack.push(CropCommand(canvas, full, crop))
    canvas.fit_image()
    assert canvas.frame_rect() == crop.adjusted(-STYLE.padding, -STYLE.padding, STYLE.padding, STYLE.padding)
    assert _is_backdrop(_on_screen(canvas, QPointF(crop.left() - STYLE.padding / 2, crop.center().y())))
    assert _is_backdrop(_on_screen(canvas, QPointF(crop.left() + 1, crop.top() + 1)))  # rounded corner
    assert _on_screen(canvas, crop.center()).lightness() > 240
    assert editor.final_image().size() == apply_beautify(canvas.render_image()).size()
    assert canvas.render_image().size() == crop.size().toSize()
    canvas.undo_stack.undo()
    assert canvas.frame_rect() == full.adjusted(-STYLE.padding, -STYLE.padding, STYLE.padding, STYLE.padding)


def test_toggling_keeps_a_zoomed_in_view_and_refits_a_fitted_one(make_editor):
    editor = make_editor()
    canvas = editor.canvas
    canvas.fit_image()
    QApplication.processEvents()
    beautify.set_enabled(True)  # e.g. from Settings: the fitted view now shows the whole backdrop
    shown = canvas.mapToScene(canvas.viewport().rect()).boundingRect()
    assert shown.contains(canvas.frame_rect())
    canvas.set_zoom(6.0)
    beautify.set_enabled(False)
    assert canvas.zoom() == pytest.approx(6.0)  # you were working up close: stay there


def test_backdrop_is_drawn_once_not_every_frame(make_editor):
    editor = make_editor()
    editor.tray.beautify.click()
    canvas = editor.canvas
    _on_screen(canvas, QPointF(0, 0))
    first = canvas._mask._backdrop[1].cacheKey()
    canvas.viewport().update()
    _on_screen(canvas, QPointF(0, 0))
    assert canvas._mask._backdrop[1].cacheKey() == first


def test_self_test_passes_with_beautify_on(qapp):
    from snipforge import selftest
    beautify.set_enabled(True)
    assert "annotation" in selftest._editor()
