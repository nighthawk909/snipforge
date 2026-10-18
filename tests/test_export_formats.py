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

    def choose(_parent, _title, _start, filters, *_selected):
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


# --- review: limits, failures keep the old file, PDFs lossless and the snip's size ------------------
def test_webp_too_big_says_why_and_keeps_the_old_file(tmp_path):
    from snipforge.editor.export import WEBP_MAX_SIDE, ExportError
    target = tmp_path / "tall.webp"
    small = QImage(10, 10, QImage.Format_ARGB32)
    small.fill(QColor("#336699"))
    assert save_image(small, target)
    before = target.read_bytes()
    tall = QImage(100, WEBP_MAX_SIDE + 1, QImage.Format_ARGB32)
    tall.fill(QColor("#336699"))
    with pytest.raises(ExportError, match="PNG or PDF"):
        save_image(tall, target)
    assert target.read_bytes() == before and list(tmp_path.iterdir()) == [target]


def test_save_falls_back_to_png_for_a_snip_too_big_for_webp(qapp, tmp_path):
    from snipforge.editor.export import WEBP_MAX_SIDE
    from snipforge.editor.window import EditorWindow
    from snipforge.library import Library
    tall = QImage(40, WEBP_MAX_SIDE + 1, QImage.Format_ARGB32)
    tall.fill(QColor("#336699"))
    lib = Library(tmp_path / "lib")
    prefs.set_export_dir(tmp_path / "out")
    prefs.set_export_format("webp")
    w = EditorWindow(tall, lib, lib.create(tall))
    w.save()
    assert w.export_path.suffix == ".png" and w.export_path.exists() and "PNG" in w.statusBar().currentMessage()
    w.close()


def test_a_failed_save_says_so_and_leaves_no_file(qapp, tmp_path, snip):
    blocked = tmp_path / "a-folder.png"
    blocked.mkdir()
    assert not save_image(snip, blocked)
    assert not save_image(snip, tmp_path / "missing" / "a.pdf")


def test_pdf_pixels_are_exact_and_fill_the_page(tmp_path):
    odd = QImage(1003, 77, QImage.Format_ARGB32)
    odd.fill(QColor("#ff0000"))
    assert save_image(odd, tmp_path / "odd.pdf")
    data = (tmp_path / "odd.pdf").read_bytes()
    assert b"/DCTDecode" not in data  # not JPEG
    w, h = (float(v) for v in re.findall(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", data)[0])
    assert abs(w - 1003 * 0.75) <= 1 and abs(h - 77 * 0.75) <= 1


def test_a_huge_pdf_page_stays_within_what_readers_open(tmp_path):
    from snipforge.editor.export import PDF_MAX_POINTS
    tall = QImage(600, 30000, QImage.Format_RGB32)
    tall.fill(QColor("#336699"))
    assert save_image(tall, tmp_path / "tall.pdf")
    w, h = (float(v) for v in
            re.findall(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", (tmp_path / "tall.pdf").read_bytes())[0])
    assert h <= PDF_MAX_POINTS + 1 and abs(w / h - 600 / 30000) < 0.001


def test_extensions_in_any_case_and_names_with_dots(tmp_path, snip):
    from snipforge.editor.export import with_suffix
    for name in ("a.WEBP", "b.Pdf", "c.JPEG", "d.JpG"):
        assert save_image(snip, tmp_path / name) and (tmp_path / name).stat().st_size > 0
    assert (tmp_path / "b.Pdf").read_bytes().startswith(b"%PDF")
    assert with_suffix(str(tmp_path / "report v1.2"), "WebP (*.webp)").name == "report v1.2.webp"
    assert with_suffix(str(tmp_path / "x.webp"), "PDF (*.pdf)").name == "x.webp"


def test_save_as_starts_on_the_current_type(editor, tmp_path, monkeypatch):
    prefs.set_export_format("pdf")
    seen = {}

    def choose(_parent, _title, start, _filters, selected=""):
        seen["start"], seen["selected"] = start, selected
        return "", ""
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    editor.save_as()
    assert seen["start"].endswith(".pdf") and seen["selected"] == "PDF (*.pdf)"
