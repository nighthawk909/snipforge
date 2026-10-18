"""Saving a snip as PNG, JPG, WebP or PDF, from Save, Save as and Settings › Saving › File format."""
import re

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QFileDialog

from snipforge import prefs
from snipforge.editor.export import EXTENSIONS, SAVE_FILTERS, save_image
from snipforge.settings import app_settings


@pytest.fixture
def snip(qapp):
    img = QImage(400, 225, QImage.Format_ARGB32)
    img.fill(QColor("#336699"))
    return img


@pytest.fixture(autouse=True)
def _default_format():
    yield
    app_settings().remove(prefs.KEYS["export_format"])
    app_settings().remove(prefs.KEYS["export_dir"])


def test_webp_is_lossless_and_keeps_transparency(tmp_path, snip):
    clear = snip.copy()
    clear.setPixelColor(0, 0, QColor(0, 0, 0, 0))
    assert save_image(clear, tmp_path / "a.webp")
    back = QImage(str(tmp_path / "a.webp"))
    assert back.size() == snip.size() and back.pixelColor(10, 10).name() == "#336699"
    assert back.pixelColor(0, 0).alpha() == 0


def test_pdf_is_one_page_the_snips_size(tmp_path, snip):
    assert save_image(snip, tmp_path / "a.pdf")
    data = (tmp_path / "a.pdf").read_bytes()
    assert data.startswith(b"%PDF")
    boxes = re.findall(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", data)
    assert len(boxes) == 1 and (float(boxes[0][0]), round(float(boxes[0][1]))) == (300.0, 169)  # 96 dpi in points


def test_every_extension_writes_something(tmp_path, snip):
    for ext in EXTENSIONS:
        assert save_image(snip, tmp_path / f"a{ext}") and (tmp_path / f"a{ext}").stat().st_size > 0, ext


def test_settings_offers_webp_and_pdf(qapp):
    from snipforge.settings_window import SettingsWindow
    w = SettingsWindow()
    for fmt in ("webp", "pdf", "png", "jpg"):
        w.format.button(fmt).click()
        assert prefs.export_format() == fmt
    w.close()


def test_prefs_accept_the_new_formats():
    for fmt in ("webp", "pdf"):
        prefs.set_export_format(fmt)
        assert prefs.export_format() == fmt
    with pytest.raises(ValueError):
        prefs.set_export_format("gif")


@pytest.fixture
def editor(qapp, tmp_path, snip):
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library
    lib = Library(tmp_path / "lib")
    prefs.set_export_dir(tmp_path / "out")
    w = EditorWindow(snip, lib, lib.create(snip))
    w.show()
    QApplication.processEvents()
    yield w
    w.close()


@pytest.mark.parametrize("fmt", ["webp", "pdf", "jpg", "png"])
def test_save_uses_the_format_from_settings(editor, tmp_path, fmt):
    prefs.set_export_format(fmt)
    editor.save()
    assert editor.export_path.suffix == f".{fmt}" and editor.export_path.parent == tmp_path / "out"
    assert editor.export_path.stat().st_size > 0


def test_save_as_offers_every_format(editor, tmp_path, monkeypatch):
    seen = {}

    def choose(_parent, _title, _start, filters):
        seen["filters"] = filters
        return str(tmp_path / "picked.webp"), "WebP (*.webp)"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    editor.save_as()
    assert seen["filters"] == SAVE_FILTERS and all(f in SAVE_FILTERS for f in ("*.webp", "*.pdf", "*.png", "*.jpg"))
    assert QImage(str(tmp_path / "picked.webp")).size() == editor.final_image().size()



def test_save_as_without_an_extension_uses_the_chosen_type(editor, tmp_path, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(tmp_path / "report"), "PDF (*.pdf)"))
    editor.save_as()
    assert editor.export_path == tmp_path / "report.pdf"
    assert (tmp_path / "report.pdf").read_bytes().startswith(b"%PDF")
