import pytest
from PySide6.QtCore import QRectF

from snipforge.library import DocumentUnreadable, Library
from snipforge.ocr import OcrLine, OcrWord
from snipforge.redact import find_sensitive, summarize


def line(*words):
    x = 0.0
    built = []
    for w in words:
        built.append(OcrWord(w, QRectF(x, 0, len(w) * 7, 12)))
        x += len(w) * 7 + 5
    return OcrLine(" ".join(words), tuple(built))


def test_library_create_save_load(tmp_path, white_image):
    lib = Library(tmp_path)
    entry = lib.create(white_image)
    assert lib.entries()[0].id == entry.id
    assert lib.load_image(entry).size() == white_image.size()
    assert lib.load_document(entry) == {}
    lib.save(entry, {"version": 1, "items": []}, white_image)
    assert lib.load_document(entry) == {"version": 1, "items": []}
    assert entry.preview_path.name == "preview.png"


def test_library_refuses_corrupt_document(tmp_path, white_image):
    lib = Library(tmp_path)
    entry = lib.create(white_image)
    (entry.path / "document.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(DocumentUnreadable):
        lib.load_document(entry)


def test_library_rendered_is_full_size(tmp_path, qapp):
    from PySide6.QtGui import QColor, QImage
    big = QImage(1600, 900, QImage.Format_ARGB32)
    big.fill(QColor("red"))
    lib = Library(tmp_path)
    entry = lib.create(big)
    lib.save(entry, {}, big)
    assert QImage(str(entry.rendered_path)).width() == 1600
    assert QImage(str(entry.preview_path)).width() == 480
    assert not list(entry.path.glob("*.tmp"))


def test_library_entries_newest_first(tmp_path, white_image):
    lib = Library(tmp_path)
    ids = [lib.create(white_image).id for _ in range(3)]
    assert [e.id for e in lib.entries()] == sorted(ids, reverse=True)
    assert len(lib.entries(limit=2)) == 2


def test_redact_finds_email_and_phone_spanning_words():
    findings = find_sensitive([line("Email", "jo@acme.com", "or", "call", "(555)", "123-4567")])
    labels = sorted(f.label for f in findings)
    assert labels == ["email", "phone"]
    phone = next(f for f in findings if f.label == "phone")
    assert phone.rect.width() > 50  # covers both "(555)" and "123-4567"


def test_redact_ignores_plain_text_and_small_numbers():
    assert find_sensitive([line("Gross", "profit", "up", "12.5%", "in", "Q3")]) == []


def test_redact_card_not_double_counted_as_phone():
    findings = find_sensitive([line("Card", "4111", "1111", "1111", "1111")])
    assert [f.label for f in findings] == ["card"]


def test_summarize():
    findings = find_sensitive([line("a@b.co", "c@d.co")])
    assert summarize(findings) == "2 emails"
