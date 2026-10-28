"""Your own stamps: add a picture, drop it like a built-in stamp, remove it; a missing file never errors."""
import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from snipforge.editor import custom_stamps
from snipforge.editor.canvas import document_version
from snipforge.editor.panel import PropertiesPanel
from snipforge.editor.stamps import STAMPS, StampItem, draw_stamp, stamp_icon
from snipforge.editor.style import Style
from tests.test_editor import drag, make_canvas


@pytest.fixture(autouse=True)
def stamps_folder(tmp_path):
    custom_stamps.set_root(tmp_path / "Stamps")
    yield tmp_path / "Stamps"
    custom_stamps.set_root(None)


@pytest.fixture
def logo(tmp_path):
    img = QImage(300, 150, QImage.Format_ARGB32)
    img.fill(QColor("#0a84ff"))
    path = tmp_path / "My Logo!.png"
    img.save(str(path))
    return path


def test_add_list_and_remove(logo, stamps_folder):
    assert custom_stamps.names() == []
    name = custom_stamps.add(logo)
    assert name == "custom:My Logo" and custom_stamps.label_of(name) == "My Logo"
    assert (stamps_folder / "My Logo.png").is_file()
    assert custom_stamps.names() == [name]
    assert custom_stamps.add(logo) == "custom:My Logo 2"  # both kept
    assert custom_stamps.image(name).width() == 300
    assert custom_stamps.remove(name) and not custom_stamps.remove(name)
    assert custom_stamps.names() == ["custom:My Logo 2"]
    assert custom_stamps.add(logo.with_suffix(".txt")) is None  # not a picture


def test_big_pictures_are_scaled_down_and_svgs_kept(tmp_path, stamps_folder):
    big = QImage(3000, 1000, QImage.Format_ARGB32)
    big.fill(QColor("red"))
    big.save(str(tmp_path / "big.png"))
    assert custom_stamps.add(tmp_path / "big.png") == "custom:big"
    assert custom_stamps.image("custom:big").width() == custom_stamps.MAX_SIDE
    svg = tmp_path / "mark.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                   '<rect width="10" height="10" fill="green"/></svg>')
    assert custom_stamps.add(svg) == "custom:mark" and (stamps_folder / "mark.svg").is_file()
    assert custom_stamps.image("custom:mark").width() == 256


def test_a_custom_stamp_on_the_snip_and_in_a_document(logo, white_image):
    name = custom_stamps.add(logo)
    canvas = make_canvas(white_image)
    canvas.set_tool("stamp")
    canvas.apply_style(stamp=name)
    stamp = drag(canvas, "stamp", (60, 50), (60, 50))
    assert isinstance(stamp, StampItem) and stamp.style.stamp == name
    state = stamp.get_state()
    assert document_version([state]) == 3
    picture = canvas.render_image()
    assert picture.pixelColor(60, 50).blue() > 200 and picture.pixelColor(60, 50).red() < 50  # the logo, drawn
    custom_stamps.remove(name)  # the file is gone: an empty frame, not an error
    pix = QPixmap(40, 40)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    draw_stamp(p, name, QRectF(0, 0, 40, 40))
    p.end()
    assert not stamp_icon(name, 24).isNull()


def test_the_panel_offers_your_stamps_and_adding_more(logo, monkeypatch):
    panel = PropertiesPanel()
    panel.load("stamp", Style(), has_selection=False)
    assert panel.custom_stamp_buttons() == {}
    name = custom_stamps.add(logo)
    QApplication.processEvents()
    assert list(panel.custom_stamp_buttons()) == [name]
    seen = []
    panel.changed.connect(seen.append)
    button = panel.custom_stamp_buttons()[name]
    QTest.mouseClick(button, Qt.LeftButton, Qt.NoModifier, button.rect().center())
    assert seen == [{"stamp": name}]
    panel.load("stamp", Style(stamp=name), has_selection=True)
    assert panel.custom_stamp_buttons()[name].isChecked()
    assert not panel.stamp_button(next(iter(STAMPS))).isChecked()
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileNames", lambda *a, **k: ([str(logo)], ""))
    panel.add_stamp_button().click()
    QApplication.processEvents()
    assert seen[-1] == {"stamp": "custom:My Logo 2"}  # added, and picked for the next drop
    assert len(panel.custom_stamp_buttons()) == 2
    panel.remove_custom_stamp(name)
    QApplication.processEvents()
    assert list(panel.custom_stamp_buttons()) == ["custom:My Logo 2"]
