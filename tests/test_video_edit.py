"""The video editor's edit (snipforge/video/edit.py): the trim, the parts cut out of the middle, a
title card and a caption, as one value; and how the saved video's frames are planned from it."""
import pytest
from PySide6.QtGui import QColor, QImage

from snipforge.video import edit as edit_module
from snipforge.video.edit import Title, VideoEdit, output_plan
from snipforge.video.timeline import FrameTimes, TrimRange

FRAMES = FrameTimes.uniform(10, 100)  # ten seconds at 10 frames a second


def test_a_plain_edit_is_the_trim_and_needs_no_new_encoding():
    e = VideoEdit.whole(FRAMES)
    assert e.kept_spans() == ((0, 100),) and e.seconds == pytest.approx(10.0)
    assert not e.needs_render and e.is_whole
    trimmed = e.with_range(TrimRange(FRAMES, 10, 90))
    assert trimmed.kept_spans() == ((10, 90),) and not trimmed.needs_render and not trimmed.is_whole


def test_cutting_out_the_middle_keeps_the_rest_and_needs_new_encoding():
    e = VideoEdit.whole(FRAMES).with_cut(30, 50)
    assert e.kept_spans() == ((0, 30), (50, 100)) and e.seconds == pytest.approx(8.0)
    assert e.needs_render and not e.is_whole
    e = e.with_cut(45, 60)  # overlapping cuts become one
    assert e.cuts == ((30, 60),) and e.kept_spans() == ((0, 30), (60, 100))
    e = e.with_cut(60, 70)  # and touching ones
    assert e.cuts == ((30, 70),)


def test_cuts_stay_inside_the_trim_and_never_take_everything():
    e = VideoEdit.whole(FRAMES).with_range(TrimRange(FRAMES, 20, 80)).with_cut(10, 30)
    assert e.cuts == ((20, 30),) and e.kept_spans() == ((30, 80),)
    with pytest.raises(ValueError):
        e.with_cut(25, 80)  # with the first cut, nothing would be left
    assert e.with_cut(85, 95) == e  # outside the trim: nothing to cut
    narrowed = e.with_range(TrimRange(FRAMES, 40, 80))  # a trim past a cut drops it
    assert narrowed.cuts == () and narrowed.kept_spans() == ((40, 80),)


def test_a_cut_can_be_put_back():
    e = VideoEdit.whole(FRAMES).with_cut(10, 20).with_cut(40, 60)
    assert e.cut_at(45) == (40, 60) and e.cut_at(30) is None
    assert e.without_cut((40, 60)).cuts == ((10, 20),)


def test_a_title_and_a_caption():
    e = VideoEdit.whole(FRAMES).with_title(Title("Quarterly report", 3.0)).with_caption("  Click Save  ")
    assert e.seconds == pytest.approx(13.0) and e.needs_render and e.caption == "Click Save"
    assert e.with_title(None).with_caption("").needs_render is False
    with pytest.raises(ValueError):
        Title("", 3.0)
    with pytest.raises(ValueError):
        Title("Too long a card", 60.0)


def test_the_plan_lists_each_saved_frame_and_where_it_comes_from():
    e = VideoEdit.whole(FRAMES).with_range(TrimRange(FRAMES, 10, 40)).with_cut(20, 30).with_title(Title("Hi", 1.0))
    plan = output_plan(e, fps=10)
    titles = [p for p in plan if p.source is None]
    assert len(titles) == 10 and titles[0].at == 0.0  # a second of title card at 10 frames a second
    video = [p for p in plan if p.source is not None]
    assert [p.source for p in video] == list(range(10, 20)) + list(range(30, 40))
    assert video[0].at == pytest.approx(1.0) and video[10].at == pytest.approx(2.0)  # the cut is closed up
    assert list(plan) == sorted(plan, key=lambda p: p.at)


def test_the_sound_follows_the_same_plan():
    e = VideoEdit.whole(FRAMES).with_range(TrimRange(FRAMES, 10, 40)).with_cut(20, 30).with_title(Title("Hi", 1.0))
    assert edit_module.sound_plan(e) == ((1.0, 1.0, 2.0), (2.0, 3.0, 4.0))  # (at, from, to) in seconds


def test_a_title_card_and_a_caption_are_drawn_on_the_frame(qapp):
    frame = QImage(640, 360, QImage.Format_ARGB32)
    frame.fill(QColor("#336699"))
    card = edit_module.title_card(Title("Quarterly report", 3.0), frame.size())
    assert card.size() == frame.size()
    assert card.pixelColor(5, 5) != QColor("#336699")  # its own background
    middle = {card.pixelColor(x, y).name() for x in range(0, 640) for y in range(150, 210, 3)}
    assert len(middle) > 1  # with the words across the middle
    captioned = edit_module.with_caption(frame, "Click Save to keep it")
    assert captioned.pixelColor(320, 20) == QColor("#336699")  # the top is untouched
    assert captioned.pixelColor(2, 355) != QColor("#336699")  # a band along the bottom
    band = {captioned.pixelColor(x, y).name() for x in range(0, 640) for y in range(330, 360, 2)}
    assert len(band) > 2  # with the words on it
    assert edit_module.with_caption(frame, "") is frame
