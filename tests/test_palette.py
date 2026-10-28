"""The tool palette: which tools the rail shows, in what order and groups, your favorites, and
labels and side. It's yours to arrange, kept in settings, and new tools in a later version never
go missing."""
import json

import pytest

from snipforge.editor import palette
from snipforge.editor.palette import TOOL_NAMES, TOOLS, Palette
from snipforge.settings import app_settings


@pytest.fixture(autouse=True)
def clean_settings():
    app_settings().remove(palette.SETTING)
    yield
    app_settings().remove(palette.SETTING)


def test_the_default_palette_is_every_tool_in_its_group():
    p = Palette.default()
    assert [name for group in p.groups for name in group] == TOOL_NAMES
    assert p.groups[0] == ["select"]
    assert p.favorites == [] and p.hidden == set() and p.labels is False and p.side == "left"
    assert p.is_default()


def test_rail_groups_put_favorites_first_and_leave_out_hidden_tools():
    p = Palette.default().with_changes(favorites=["crop", "arrow"], hidden={"pixelate", "arrow"})
    groups = p.rail_groups()
    assert groups[0] == ["crop"]  # a favorite that's hidden isn't shown anywhere
    flat = [name for group in groups[1:] for name in group]
    assert "pixelate" not in flat and "arrow" not in flat
    assert "crop" in flat  # a favorite still has its place in its own group


def test_empty_groups_are_dropped_from_the_rail():
    p = Palette.default().with_changes(hidden={"select"})
    assert all(p.rail_groups()) and ["select"] not in p.rail_groups()


def test_saved_and_loaded_again():
    p = Palette.default().with_changes(favorites=["text"], hidden={"line"}, labels=True, side="right")
    p = p.moved("crop", 0, 1)  # crop joins the first group after Select
    palette.save(p)
    again = palette.load()
    assert again == p
    assert again.groups[0] == ["select", "crop"]


def test_damaged_settings_read_as_the_default():
    for raw in ("", "nonsense", "[]", '{"groups": "x"}', json.dumps({"groups": [["no-such-tool"]]})):
        app_settings().setValue(palette.SETTING, raw)
        assert palette.load().is_default(), raw


def test_tools_this_version_adds_are_put_back_in_their_group():
    stored = {"groups": [[name for name in group if name != "magnify"] for group in Palette.default().groups],
              "favorites": ["select"], "hidden": ["erase"], "labels": False, "side": "left"}
    app_settings().setValue(palette.SETTING, json.dumps(stored))
    p = palette.load()
    assert [n for g in p.groups for n in g].count("magnify") == 1
    assert "magnify" in p.groups[Palette.default_group_of("magnify")]
    assert p.favorites == ["select"] and p.hidden == {"erase"}


def test_unknown_tools_and_duplicates_are_dropped_and_lists_capped():
    stored = {"groups": [["select", "select", "future-tool"], TOOL_NAMES[1:]], "favorites": ["x", "crop", "crop"],
              "hidden": ["y", "blur"], "labels": "yes", "side": "top"}
    app_settings().setValue(palette.SETTING, json.dumps(stored))
    p = palette.load()
    assert p.groups[0] == ["select"] and [n for g in p.groups for n in g] == TOOL_NAMES
    assert p.favorites == ["crop"] and p.hidden == {"blur"}
    assert p.labels is False and p.side == "left"


def test_moving_a_tool_between_and_within_groups():
    p = Palette.default()
    moved = p.moved("crop", 1, 0)  # to the front of the shapes group
    assert moved.groups[1][0] == "crop" and "crop" not in moved.groups[-1]
    same = moved.moved("crop", 1, 3)
    assert same.groups[1][3] == "crop"
    assert sorted(n for g in same.groups for n in g) == sorted(TOOL_NAMES)
    assert p.moved("crop", 99, 0).groups[-1][0] == "crop"  # out of range: the last group
    assert p.moved("crop", 1, 99).groups[1][-1] == "crop"  # past the end: last in the group


def test_dividers_can_be_added_and_removed():
    p = Palette.default()
    split = p.split_group(1, 3)  # arrow, line, rect | ellipse, spotlight, magnify
    assert split.groups[1] == ["arrow", "line", "rect"] and split.groups[2] == ["ellipse", "spotlight", "magnify"]
    assert split.join_groups(1) == p
    assert p.split_group(1, 0) == p and p.split_group(1, 6) == p  # nothing to split off


def test_favorites_toggle_and_keep_their_order():
    p = Palette.default().toggled_favorite("text").toggled_favorite("crop")
    assert p.favorites == ["text", "crop"]
    assert p.toggled_favorite("text").favorites == ["crop"]
    assert p.rail_groups()[0] == ["text", "crop"]


def test_changes_are_announced_to_every_editor(qapp):
    seen = []
    palette.notifier().changed.connect(lambda: seen.append(1))
    palette.save(Palette.default().with_changes(labels=True))
    assert seen == [1]


def test_tool_titles_and_keys():
    assert palette.title_of("select") == "Select and move"
    assert palette.key_of("select") == "V"
    assert palette.key_of("move") == "G"
    assert all(palette.key_of(name) for name in TOOL_NAMES)
    assert len({palette.key_of(name) for name in TOOL_NAMES}) == len(TOOL_NAMES)  # every tool its own key
    assert TOOLS[1] is None  # the divider after Select is still there for the window
