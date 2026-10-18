"""The "App name and date" stamp: where a snip came from and when, as text you can edit."""
import datetime as dt

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge.editor.app_stamp import stamp_text, when
from snipforge.editor.text_items import TextItem
from snipforge.library import Library
from snipforge.winrects import WindowInfo

TAKEN = dt.datetime(2026, 10, 17, 15, 4)


def test_the_text_names_the_window_or_app_and_when():
    assert when(TAKEN) == "Oct 17, 2026, 3:04 PM"
    assert when(dt.datetime(2026, 1, 2, 0, 30)) == "Jan 2, 2026, 12:30 AM"
    assert stamp_text("Pricing.xlsx - Excel", "Excel", TAKEN) == "Pricing.xlsx - Excel · Oct 17, 2026, 3:04 PM"
    assert stamp_text("", "Edge", TAKEN) == "Edge · Oct 17, 2026, 3:04 PM"  # private browsing: no title kept
    assert stamp_text("Bank - InPrivate - Microsoft Edge", "Edge", TAKEN).startswith("Edge · ")  # an older snip
    assert stamp_text("", "", TAKEN) == "Oct 17, 2026, 3:04 PM"
    assert stamp_text("  Spaced\nout  ", "", TAKEN).startswith("Spaced out · ")


@pytest.fixture
def make_editor(qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage

    from snipforge.editor.window import EditorWindow
    lib = Library(tmp_path)
    made = []

    def build(source=None, entry=True, size=(1200, 400)):
        image = QImage(*size, QImage.Format_ARGB32)
        image.fill(QColor("white"))
        e = lib.create(image, source=source) if entry else None
        w = EditorWindow(image, lib, e)
        w.resize(1100, 700)
        w.show()
        QApplication.processEvents()
        made.append(w)
        return w
    yield build
    for w in made:
        w.close()


def stamps(canvas):
    return [i for i in canvas.annotations() if isinstance(i, TextItem)]


def test_the_button_adds_an_editable_stamp_inside_the_snip(make_editor):
    editor = make_editor(WindowInfo(None, "Quarterly report - Word", "Word"))
    editor.canvas.set_tool("stamp")
    button = editor.panel.source_stamp_button()
    assert button.isEnabled()
    QTest.mouseClick(button, Qt.LeftButton)
    [stamp] = stamps(editor.canvas)
    assert stamp.toPlainText() == stamp_text("Quarterly report - Word", "Word", editor._entry.created)
    assert QRectF(0, 0, 1200, 400).contains(stamp.sceneBoundingRect())
    assert stamp.sceneBoundingRect().bottom() > 300  # the bottom-left corner
    assert editor.canvas.selected_annotations() == [stamp]
    editor.canvas.undo_stack.undo()
    assert stamps(editor.canvas) == []


def test_private_browsing_stays_private(make_editor):
    editor = make_editor(WindowInfo(None, "", "Edge"))  # capture already blanked the title
    editor.canvas.set_tool("stamp")
    QTest.mouseClick(editor.panel.source_stamp_button(), Qt.LeftButton)
    assert stamps(editor.canvas)[0].toPlainText().startswith("Edge · ")


def test_no_library_entry_no_stamp(make_editor):
    editor = make_editor(entry=False)
    editor.canvas.set_tool("stamp")
    assert not editor.panel.source_stamp_button().isEnabled()
    assert "library" in editor.panel.source_stamp_button().toolTip().lower()


def test_a_small_snip_gets_smaller_text(make_editor):
    editor = make_editor(WindowInfo(None, "A rather long window title - Some Application", "App"), size=(160, 120))
    editor.canvas.set_tool("stamp")
    QTest.mouseClick(editor.panel.source_stamp_button(), Qt.LeftButton)
    [stamp] = stamps(editor.canvas)
    assert stamp.style.font_size < 12 and stamp.sceneBoundingRect().left() >= 0
