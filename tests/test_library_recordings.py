"""Recordings in the Library: listed beside snips once finished, with their length; opened in the video
editor; never read for text; and harmless to older SnipForge versions sharing the folder."""
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QSize
from PySide6.QtGui import QColor, QGuiApplication, QImage

from snipforge.library import RECORDING, RECORDING_DETAILS, Library
from snipforge.video import clip, recordings


def white() -> QImage:
    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    return image


def finished_recording(lib: Library, seconds: float = 1.0):
    path = recordings.new_recording(lib.root)
    clip.make_test_clip(path, seconds=seconds)
    recordings.write_details(path, seconds, (320, 240))
    return path


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / "Library")


def test_a_finished_recording_is_listed_beside_snips(lib, qapp):
    snip = lib.create(white())
    video = finished_recording(lib)

    found = {s.entry.id: s.entry for s in lib.scan()}

    assert found[snip.id].kind == "snip" and found[video.parent.name].kind == "recording"
    assert found[video.parent.name].recording_path == video


def test_a_recording_still_being_written_isnt_listed(lib, qapp):
    path = recordings.new_recording(lib.root)
    path.write_bytes(b"\0" * 1000)  # the recorder is still writing it: no details yet

    assert [s.entry.id for s in lib.scan()] == [] and lib.get(path.parent.name) is None


def test_entries_lists_only_snips_as_before(lib, qapp):
    snip = lib.create(white())
    finished_recording(lib)

    assert [e.id for e in lib.entries()] == [snip.id]  # callers that open images never get a video


def test_get_knows_which_kind_it_is(lib, qapp):
    snip, video = lib.create(white()), finished_recording(lib)

    assert lib.get(snip.id).kind == "snip" and lib.get(video.parent.name).kind == "recording"


def test_a_recordings_details_are_read_back(lib, qapp):
    video = finished_recording(lib, seconds=1.0)

    details = lib.recording_details(lib.get(video.parent.name))

    assert details.duration == pytest.approx(1.0) and details.size == (320, 240)


@pytest.mark.parametrize("damage", [b"not json", b"[1, 2]", b'{"duration": "long", "width": -3}'])
def test_damaged_details_still_list_the_recording_without_a_length(lib, qapp, damage):
    video = finished_recording(lib)
    (video.parent / RECORDING_DETAILS).write_bytes(damage)

    entry = lib.get(video.parent.name)

    assert entry.kind == "recording" and lib.recording_details(entry).duration is None


def test_a_recording_has_no_image_to_load(lib, qapp):
    video = finished_recording(lib)

    with pytest.raises(OSError, match="recording"):
        lib.load_image(lib.get(video.parent.name))


def test_details_are_written_whole_or_not_at_all(tmp_path):
    path = tmp_path / RECORDING

    recordings.write_details(path, 12.345, (1280, 720))

    data = json.loads((tmp_path / RECORDING_DETAILS).read_text(encoding="utf-8"))
    assert data == {"duration": 12.345, "width": 1280, "height": 720, "version": 1}
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())


def test_details_are_only_written_beside_a_library_recording(tmp_path):
    recordings.write_details(tmp_path / "something else.mp4", 3.0, (320, 240))

    assert not (tmp_path / RECORDING_DETAILS).exists()


def test_a_recording_is_never_read_for_text(lib, qapp):
    from snipforge.indexer import TextIndexer

    indexer = TextIndexer(lib)
    try:
        video = finished_recording(lib)
        assert indexer.index(lib.get(video.parent.name)) is False
    finally:
        indexer.close()


def test_a_cards_thumbnail_is_made_from_the_video_once(lib, qapp):
    from snipforge.library_view import _Loaded, _ThumbJob

    video = finished_recording(lib)
    entry = lib.get(video.parent.name)
    got = []
    signals = _Loaded()
    signals.done.connect(lambda key, image: got.append(image))

    _ThumbJob(("k",), str(entry.recording_path), QSize(232, 136), str(entry.path / "preview.png"), signals,
              video=True).run()

    assert got and got[0] is not None and (got[0].width(), got[0].height()) == (232, 136)
    assert (entry.path / "preview.png").exists()  # next time it's read from there
    assert entry.preview_path == entry.path / "preview.png"


def test_a_video_that_cant_be_read_isnt_tried_on_every_repaint(lib, qapp):
    from snipforge.library_search import Item
    from snipforge.library_view import SnipGrid

    video = finished_recording(lib)
    video.write_bytes(b"not a video" * 100)
    entry = lib.get(video.parent.name)
    delegate = SnipGrid().itemDelegate()
    item = Item(entry, entry.created, lib.meta(entry), duration=1.0)
    started = []
    delegate._pool.start = lambda job: started.append(job)
    size = QSize(232, 136)

    assert delegate._thumb(item, size, 1.0) is None and len(started) == 1
    started[0].run()  # the card keeps its placeholder
    qapp.processEvents()
    assert delegate._thumb(item, size, 1.0) is None and len(started) == 1  # not opened again
    assert not (entry.path / "preview.png").exists()


def test_a_recording_card_paints_its_length(lib, qapp):
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QStyleOptionViewItem

    from snipforge.library_search import Item
    from snipforge.library_view import CardDelegate, LibraryModel, SnipGrid

    video = finished_recording(lib, seconds=65.0)
    entry = lib.get(video.parent.name)
    grid = SnipGrid()
    model = LibraryModel()
    item = Item(entry, entry.created, lib.meta(entry), duration=65.0)
    model.set_groups([("Today", [item])])
    grid.setModel(model)
    delegate = grid.itemDelegate()
    assert isinstance(delegate, CardDelegate)
    canvas = QImage(300, 260, QImage.Format.Format_ARGB32)
    painter = QPainter(canvas)
    option = QStyleOptionViewItem()
    option.rect = canvas.rect()
    option.font = grid.font()
    delegate._paint_card(painter, option, item)  # a play badge and "1:05": no errors, nothing missing
    painter.end()
    assert delegate.duration_text(item) == "1:05"


# --- in the Library window and the app ------------------------------------------------------------

def test_copying_a_recording_puts_the_file_on_the_clipboard(lib, qapp):
    from snipforge.home import HomeWindow

    video = finished_recording(lib)
    home = HomeWindow(lib, on_new_snip=lambda: None, on_open_entry=lambda entry_id: None)
    try:
        home._copy(video.parent.name)
        mime: QMimeData = QGuiApplication.clipboard().mimeData()
        assert [Path(u.toLocalFile()) for u in mime.urls()] == [video]
    finally:
        home.close()


def test_the_library_window_lists_a_recording_with_its_length(lib, qapp):
    from snipforge.home import HomeWindow

    lib.create(white())
    video = finished_recording(lib, seconds=1.0)
    home = HomeWindow(lib, on_new_snip=lambda: None, on_open_entry=lambda entry_id: None)
    try:
        home.refresh()
        items = {i.entry.id: i for i in home._items}
        assert items[video.parent.name].duration == pytest.approx(1.0)
        assert len(items) == 2
    finally:
        home.close()


def test_searching_for_recording_or_video_finds_recordings(lib, qapp):
    from snipforge.library_search import Item

    video = finished_recording(lib)
    entry = lib.get(video.parent.name)
    snip = lib.create(white())

    assert "recording" in Item(entry, entry.created, lib.meta(entry)).haystack
    assert "video" not in Item(snip, snip.created, lib.meta(snip)).haystack
