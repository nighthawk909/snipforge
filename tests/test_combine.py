"""Combine images: snips side by side, stacked, in a grid or as numbered steps with captions, from the
Library's selection or the editor; the result is a new snip that opens in the editor, with step
numbers and captions as editable markup."""
import time

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from snipforge import combine
from snipforge.combine import Options
from snipforge.library import Library


def picture(width, height, color="#3366cc"):
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(color))
    return image


# --- the layouts ----------------------------------------------------------------------------------
def test_side_by_side_shrinks_to_the_smallest_height_and_never_enlarges():
    a, b = picture(400, 300), picture(200, 100, "#e11d48")
    made = combine.combine([a, b], Options(layout="row", gap=10))
    # a shrinks to 133 × 100; b stays; a 10 px gap and margin all round
    assert made.image.size().toTuple() == (10 + 133 + 10 + 200 + 10, 10 + 100 + 10)
    assert made.items == () and made.image.pixelColor(5, 5).name() == "#ffffff"
    natural = combine.combine([a, b], Options(layout="row", gap=10, same_size=False))
    assert natural.image.size().toTuple() == (10 + 400 + 10 + 200 + 10, 10 + 300 + 10)
    assert natural.image.pixelColor(10 + 400 + 10 + 100, 10 + 150).name() == "#e11d48"  # b, centred


def test_stacked_shrinks_to_the_smallest_width():
    made = combine.combine([picture(400, 300), picture(200, 100)], Options(layout="column", gap=0))
    assert made.image.size().toTuple() == (200, 150 + 100)


def test_a_grid_fills_rows_of_the_chosen_number_of_columns():
    pictures = [picture(100, 50) for _ in range(5)]
    made = combine.combine(pictures, Options(layout="grid", columns=3, gap=10))
    assert made.image.size().toTuple() == (10 + 3 * 100 + 2 * 10 + 10, 10 + 2 * 50 + 10 + 10)
    assert made.image.pixelColor(10 + 100 + 10 + 50, 10 + 50 + 10 + 25).name() == "#3366cc"  # the 5th
    assert made.image.pixelColor(10 + 2 * 110 + 50, 10 + 60 + 25).name() == "#ffffff"  # no 6th


def test_numbered_steps_are_markup_the_editor_can_change(qapp):
    made = combine.combine([picture(300, 100), picture(300, 100), picture(300, 100)],
                           Options(layout="steps", captions=("Open Settings", "", "Click Save")))
    steps = [i for i in made.items if i["kind"] == "step"]
    texts = [i for i in made.items if i["kind"] == "text"]
    assert [s["number"] for s in steps] == [1, 2, 3]
    assert [t["text"] for t in texts] == ["Open Settings", "Click Save"]  # an empty caption adds nothing
    assert made.image.height() == 12 + 3 * (combine.STEP_ROW + 100) + 2 * 12 + 12
    document, rendered = made.finished()
    assert len(document["items"]) == 5 and rendered.size() == made.image.size()
    assert rendered != made.image  # the badges and captions are drawn into its preview


def test_captions_on_a_dark_background_are_light(qapp):
    made = combine.combine([picture(100, 40), picture(100, 40)], Options(layout="steps", background="dark",
                                                                         captions=("One", "Two")))
    assert {i["style"]["color"] for i in made.items if i["kind"] == "text"} == {"#ffffff"}


def test_a_transparent_background():
    made = combine.combine([picture(50, 50), picture(50, 50)], Options(background="none"))
    assert made.image.pixelColor(2, 2).alpha() == 0


def test_a_huge_result_shrinks_its_pictures_and_keeps_its_markup_readable(qapp, monkeypatch):
    monkeypatch.setattr(combine, "MAX_SIDE", 600)
    sizes = [(2000, 1000)] * 4  # big pictures: markup scaled up for them, then the pictures shrunk to fit
    laid = combine.plan(sizes, Options(layout="steps", gap=0, captions=("A",) * 4))
    assert max(laid.width, laid.height) <= 600
    rows = [r.top() for r in laid.rects]
    steps = [i["pos"][1] for i in laid.items if i["kind"] == "step"]
    assert all(rows[n] > steps[n] and (n == 0 or steps[n] > rows[n - 1] + laid.rects[n - 1].height())
               for n in range(4))  # each number sits between the picture before it and its own


def test_steps_on_big_pictures_get_bigger_numbers_and_captions():
    laid = combine.plan([(1920, 1080)] * 2, Options(layout="steps", captions=("Open it", "")))
    badge = next(i for i in laid.items if i["kind"] == "step")
    caption = next(i for i in laid.items if i["kind"] == "text")
    assert badge["style"]["width"] == combine.STEP_BADGE_WIDTH * 2 and caption["style"]["font_size"] == 40
    small = combine.plan([(600, 300)] * 2, Options(layout="steps"))
    assert next(i for i in small.items if i["kind"] == "step")["style"]["width"] == combine.STEP_BADGE_WIDTH


def test_a_result_qt_could_not_read_back_is_shrunk_by_area_too():
    sizes = [(3840, 2160)] * 9  # a 3 × 3 grid of 4K screenshots: 300 MB as one image
    width, height = combine.measure(sizes, Options(layout="grid", columns=3, gap=0, same_size=False))
    assert width * height <= combine.MAX_PIXELS and max(width, height) <= combine.MAX_SIDE


def test_measuring_never_scales_a_picture():
    """Sizes in, sizes out: a big Combine window's preview never rescales full-size pictures."""
    assert combine.measure([(400, 300), (200, 100)], Options(layout="row", gap=10)) == (10 + 133 + 10 + 200 + 10, 120)


def test_a_caption_is_never_cut_off_by_a_narrow_picture(qapp):
    made = combine.combine([picture(160, 60), picture(160, 60)],
                           Options(layout="steps", captions=("A caption much wider than the picture below it", "")))
    document, rendered = made.finished()
    text = next(i for i in made.items if i["kind"] == "text")
    from snipforge.combine import _caption_width

    assert text["pos"][0] + _caption_width(text["text"], text["style"]["font_size"]) <= made.image.width()


def test_the_preview_is_the_real_layout_drawn_small(qapp):
    big = [picture(2000, 1000), picture(600, 300, "#e11d48")]
    small = [i.scaled(200, 100) if i.width() > 600 else i.scaled(60, 30) for i in big]
    options = Options(layout="row", same_size=False, gap=12)
    drawn = combine.preview(small, [i.size() for i in big], options, 500)
    width, height = combine.measure(big, options)
    assert abs(drawn.width() / drawn.height() - width / height) < 0.02  # the same shape
    assert drawn.pixelColor(drawn.width() - 20, drawn.height() // 10).name() == "#ffffff"  # the small one is small
    steps = combine.preview(small, [i.size() for i in big], Options(layout="steps", captions=("One", "Two")), 500)
    assert steps.size() != QImage().size()


@pytest.mark.parametrize("pictures, options", [([picture(10, 10)], Options()),
                                                 ([picture(10, 10)] * (combine.MAX_IMAGES + 1), Options()),
                                                 ([picture(10, 10)] * 2, Options(layout="spiral")),
                                                 ([picture(10, 10), QImage()], Options())])
def test_what_cannot_be_combined_says_so(pictures, options):
    with pytest.raises(ValueError):
        combine.combine(pictures, options)


# --- the Combine window ----------------------------------------------------------------------------
@pytest.fixture
def library(tmp_path):
    lib = Library(tmp_path / "Library")
    made = []
    for n, color in enumerate(("#3366cc", "#e11d48", "#16a34a", "#f59e0b")):
        made.append(lib.create(picture(200 + 20 * n, 100, color)))
        time.sleep(0.01)
    lib.ids = [e.id for e in made]
    return lib


@pytest.fixture
def dialog(qapp, library):
    from snipforge.combine_dialog import CombineDialog
    d = CombineDialog(library, library.ids[:2])
    d.show()
    yield d
    d.close()


def click(widget):
    QTest.mouseClick(widget, Qt.LeftButton)


def test_the_window_starts_with_the_chosen_snips_and_previews_them(dialog, library):
    assert dialog.ids() == library.ids[:2] and dialog.combine_button.isEnabled()
    dialog._redraw.timeout.emit()
    assert not dialog.preview.pixmap().isNull() and "pixels" in dialog.size_label.text()


def test_combine_needs_two_snips(dialog):
    dialog.pictures.setCurrentRow(0)
    click(dialog.remove_button)
    assert dialog.ids() and not dialog.combine_button.isEnabled()
    dialog._redraw.timeout.emit()
    assert "at least two" in dialog.preview.text()


def test_snips_are_added_from_the_library_and_moved(dialog, library, monkeypatch):
    from snipforge.combine_dialog import SnipPicker

    def choose(picker):
        assert {picker.snips.item(n).data(Qt.UserRole) for n in range(picker.snips.count())} == set(library.ids[2:])
        for n in range(picker.snips.count()):
            picker.snips.item(n).setSelected(True)
        return QDialog.Accepted
    monkeypatch.setattr(SnipPicker, "exec", choose)
    click(dialog.add_button)
    assert len(dialog.ids()) == 4
    dialog.pictures.setCurrentRow(3)
    moved = dialog.ids()[3]
    click(dialog.up_button)
    assert dialog.ids()[2] == moved and dialog.pictures.currentRow() == 2


def test_a_recording_or_a_snip_already_there_is_not_added(dialog, library):
    assert not dialog._add(library.ids[0]) and not dialog._add("no-such-snip")


def test_enter_in_a_caption_goes_on_to_the_next_step(dialog):
    click(dialog.layout_choice.button("steps"))
    dialog.pictures.setCurrentRow(0)
    QTest.keyClicks(dialog.caption, "First")
    QTest.keyClick(dialog.caption, Qt.Key_Return)
    assert dialog.isVisible() and dialog.combined is None  # not combined yet
    assert dialog.pictures.currentRow() == 1 and dialog.caption.text() == ""


def test_more_than_twenty_snips_are_capped_and_said_so(qapp, tmp_path):
    from snipforge.combine_dialog import CombineDialog
    lib = Library(tmp_path / "Many")
    ids = []
    for _ in range(combine.MAX_IMAGES + 2):
        ids.append(lib.create(picture(40, 20)).id)
        time.sleep(0.002)
    d = CombineDialog(lib, ids)
    assert len(d.ids()) == combine.MAX_IMAGES and d.combine_button.isEnabled() and not d.add_button.isEnabled()
    assert "2 snips left out" in d.note.text()
    d.close()


def test_layout_choices_show_their_own_settings(dialog):
    click(dialog.layout_choice.button("grid"))
    assert not dialog.columns_row.isHidden() and dialog.caption_row.isHidden()
    click(dialog.layout_choice.button("steps"))
    assert dialog.columns_row.isHidden() and not dialog.caption_row.isHidden()
    assert dialog.pictures.item(0).text() == "1. "


def test_numbered_steps_with_captions_typed_in(dialog):
    click(dialog.layout_choice.button("steps"))
    dialog.pictures.setCurrentRow(0)
    QTest.keyClicks(dialog.caption, "Open the menu")
    dialog.pictures.setCurrentRow(1)
    assert dialog.caption.text() == ""
    QTest.keyClicks(dialog.caption, "Pick Export")
    assert dialog.pictures.item(1).text() == "2. Pick Export"
    click(dialog.combine_button)
    assert dialog.combined is not None
    assert [i.get("text") for i in dialog.combined.items if i["kind"] == "text"] == ["Open the menu", "Pick Export"]


# --- where it starts, and where it ends -------------------------------------------------------------
@pytest.fixture
def forge(qapp, library, monkeypatch):
    from snipforge import app as app_module
    monkeypatch.setattr(app_module.HotkeyListener, "start", lambda self: True)
    monkeypatch.setattr(app_module.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_module.UpdateFlow, "start", lambda self: None)
    f = app_module.SnipForgeApp(qapp, library)
    f.indexer._recognize = lambda image: ""
    yield f
    for window in list(f._windows):
        window.close()
    f.indexer.close()


def test_a_combined_snip_is_a_new_snip_that_opens_in_the_editor(forge, library, monkeypatch):
    from snipforge import combine_dialog
    made = combine.combine([picture(100, 40), picture(100, 40)], Options(layout="steps", captions=("A", "B")))
    seen = []
    monkeypatch.setattr(combine_dialog, "open_combine", lambda lib, ids, parent=None: seen.append(ids) or made)
    forge.combine(library.ids[:2])
    assert seen == [library.ids[:2]] and len(library.entries()) == 5
    [editor] = forge._windows
    assert sorted(i.kind for i in editor.canvas.annotations()) == ["step", "step", "text", "text"]
    newest = library.entries()[0]
    assert len(library.load_document(newest)["items"]) == 4


def test_cancelling_makes_nothing(forge, library, monkeypatch):
    from snipforge import combine_dialog
    monkeypatch.setattr(combine_dialog, "open_combine", lambda *a, **k: None)
    forge.combine(library.ids[:2])
    assert len(library.entries()) == 4 and forge._windows == []


def test_the_library_offers_combine_for_two_or_more_snips(qapp, library, monkeypatch):
    from snipforge.home import HomeWindow
    asked = []
    home = HomeWindow(library, lambda: None, lambda _id: None, on_combine=lambda ids, window: asked.append(ids))
    home.resize(1100, 700)
    home.show()
    grid = home.grid
    model = grid.selectionModel()
    for row in (1, 3):  # row 0 is the day's heading
        model.select(grid.model().index(row, 0), QItemSelectionModel.Select)
    shown = []
    listed = staticmethod(lambda menu, at: shown.append([x.text() for x in menu.actions()]))
    monkeypatch.setattr(HomeWindow, "_show_menu", listed)  # instead of the menu (QMenu.exec can't be replaced)
    home._context_menu(grid.visualRect(grid.model().index(1, 0)).center())
    assert "Combine 2 snips…" in shown[0]
    home.activateWindow()
    assert QTest.qWaitForWindowActive(home)
    QTest.keyClick(home, Qt.Key_G, Qt.ControlModifier)
    assert len(asked) == 1 and asked[0] == sorted(asked[0])  # oldest first: step 1 was snipped first
    home.close()


def test_a_combined_snip_that_cannot_be_saved_whole_is_not_left_behind(forge, library, monkeypatch):
    from snipforge import combine_dialog
    made = combine.combine([picture(100, 40), picture(100, 40)], Options(layout="steps", captions=("A", "B")))
    monkeypatch.setattr(combine_dialog, "open_combine", lambda *a, **k: made)
    trashed = []
    monkeypatch.setattr(library, "save", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(library, "move_to_trash", lambda entry: trashed.append(entry.id) or True)
    forge.combine(library.ids[:2])
    assert len(trashed) == 1 and forge._windows == []


def test_the_editor_combines_its_snip_after_saving_it(forge, library):
    entry = library.get(library.ids[0])
    forge.open_entry(entry.id)
    [editor] = forge._windows
    asked = []
    editor._on_combine = lambda entry_id, window: asked.append((entry_id, window))
    assert editor.commands.combine_button.isVisibleTo(editor)
    click(editor.commands.combine_button)
    assert asked == [(entry.id, editor)]
    assert (entry.path / "document.json").exists()  # saved first, so the combined image shows it as it is


def test_an_editor_that_cannot_save_stops_before_combining(forge, library, monkeypatch):
    forge.open_entry(library.ids[0])
    [editor] = forge._windows
    asked = []
    editor._on_combine = lambda entry_id, window: asked.append(entry_id)
    monkeypatch.setattr(library, "save", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    editor.combine()
    assert asked == [] and "can't be combined" in editor.statusBar().currentMessage()


def test_an_editor_without_a_library_snip_has_no_combine(qapp, library):
    from snipforge.editor.window import EditorWindow
    editor = EditorWindow(picture(100, 50), library, None)
    assert not editor.commands.combine_button.isVisibleTo(editor)
    editor.close()
