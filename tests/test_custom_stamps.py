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


def test_a_stamp_name_from_a_document_never_reaches_outside_the_stamps_folder(logo, stamps_folder, tmp_path):
    assert custom_stamps.add(logo) == "custom:My Logo"
    outside = QImage(10, 10, QImage.Format_ARGB32)
    outside.fill(QColor("red"))
    outside.save(str(tmp_path / "secret.png"))
    for name in ("custom:../secret", f"custom:{tmp_path / 'secret'}", "custom:\\\\server\\share\\secret",
                 "custom:C:secret", "custom:", "custom: My Logo"):
        assert custom_stamps.image(name) is None, name
        assert not custom_stamps.remove(name), name
    assert custom_stamps.image("custom:My Logo") is not None


def test_an_svg_that_links_to_other_files_is_not_added(tmp_path, stamps_folder):
    good = tmp_path / "good.svg"
    good.write_text('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                    'width="20" height="20"><defs><circle id="c" cx="10" cy="10" r="8"/></defs>'
                    '<use xlink:href="#c" fill="red"/><image href="data:image/png;base64,AAAA"/></svg>')
    assert custom_stamps.add(good) == "custom:good"
    for n, body in enumerate(('<image href="file:///etc/passwd"/>', "<image xlink:href='\\\\server\\x.png'/>",
                              '<rect style="fill: url(other.svg#g)"/>', '<image href = "pic.png"/>')):
        bad = tmp_path / f"bad{n}.svg"
        bad.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">{body}</svg>')
        assert custom_stamps.add(bad) is None, body
    entity = tmp_path / "entity.svg"
    entity.write_text('<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg>&x;</svg>')
    assert custom_stamps.add(entity) is None
    assert custom_stamps.names() == ["custom:good"]


def test_removing_a_stamp_from_the_panel_asks_first(logo, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    name = custom_stamps.add(logo)
    panel = PropertiesPanel()
    answers = [QMessageBox.No, QMessageBox.Yes]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answers.pop(0))
    panel._confirm_remove_stamp(name)
    assert custom_stamps.names() == [name]
    panel._confirm_remove_stamp(name)
    assert custom_stamps.names() == []
