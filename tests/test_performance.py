"""Speed and footprint (snipforge/perf.py, docs/PERFORMANCE.md): what starting SnipForge loads, the
budgets the packaged self-test checks, and the Library reading only what changed."""
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from snipforge import library as library_module
from snipforge import perf, selftest
from snipforge.library import Entry, Library

ROOT = Path(__file__).resolve().parents[1]


def in_a_fresh_python(code: str) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=ROOT, env=env)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_starting_snipforge_loads_only_what_it_needs():
    found = in_a_fresh_python(
        "import json, sys; import snipforge.app; from snipforge import perf; "
        "print(json.dumps({'heavy': sorted(perf.heavy_modules()), 'modules': len(sys.modules)}))")
    assert found["heavy"] == []  # the editor, recording, OCR's asyncio and so on load when first used
    if perf.module_budget() is not None:
        assert found["modules"] <= perf.module_budget()


def test_the_editor_loads_soon_after_start_up(qapp, tmp_path, monkeypatch):
    from snipforge import app as app_module
    loaded = []
    monkeypatch.setattr(app_module, "_load_editor", lambda: loaded.append(True))
    monkeypatch.setattr(app_module.HotkeyListener, "start", lambda self: True)
    monkeypatch.setattr(app_module.UpdateFlow, "start", lambda self: None)
    forge = app_module.SnipForgeApp(qapp, Library(tmp_path))
    perf.pump(app_module.EDITOR_PRELOAD_MS / 1000 + 0.3)
    assert loaded == [True]
    forge.indexer.close()


def test_starting_up_is_within_budget():
    """A whole start-up, timed in a fresh Python (generous budgets: CI machines are slow)."""
    measured = in_a_fresh_python(
        "import json, os, sys, tempfile\n"
        "os.environ['SNIPFORGE_LOG_FOLDER'] = tempfile.mkdtemp()\n"
        "from PySide6.QtCore import QSettings\n"
        "QSettings.setDefaultFormat(QSettings.IniFormat)\n"
        "QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp())\n"
        "from snipforge import perf\n"
        "r = perf.startup()\n"
        "print(json.dumps({'startup_s': r['startup_s'], 'startup_memory_mb': r['memory_mb'],"
        " 'heavy': r['heavy_modules']}))")
    assert measured.pop("heavy") == []
    if perf.memory_budget() is None:  # off Windows: peak resident size, reported but not budgeted
        measured.pop("startup_memory_mb")
    assert perf.over_budget(measured) == []


def test_over_budget_names_what_is_over():
    assert perf.over_budget({"startup_s": 0.2, "library_open_s": 9.0, "unbudgeted": 99}) == \
        [f"library_open_s is 9.0 (budget {perf.BUDGETS['library_open_s']})"]


def test_memory_is_measured_on_every_platform(off_windows, monkeypatch):
    assert 5 < perf.memory_mb() < 4000
    off_windows(perf)

    class Usage:
        ru_maxrss = 80 * 1024  # KB on Linux

    fake = type(sys)("resource")
    fake.RUSAGE_SELF = 0
    fake.getrusage = lambda who: Usage
    monkeypatch.setitem(sys.modules, "resource", fake)
    assert perf.memory_mb() == 80.0
    assert perf.memory_budget() is None and perf.module_budget() is None


# --- the packaged self-test's budgets ------------------------------------------------------------
def test_the_self_test_fails_when_start_up_loaded_too_much(monkeypatch):
    monkeypatch.setattr(perf, "heavy_modules", lambda: {"snipforge.editor.window"})
    with pytest.raises(RuntimeError, match="loaded at start: snipforge.editor.window"):
        selftest._startup_imports()
    monkeypatch.setattr(perf, "heavy_modules", lambda: set())
    monkeypatch.setattr(perf, "module_budget", lambda: 10)
    with pytest.raises(RuntimeError, match="modules at start"):
        selftest._startup_imports()
    monkeypatch.setattr(perf, "module_budget", lambda: None)  # off Windows: only the heavy ones checked
    assert "none of the heavy ones" in selftest._startup_imports()


def test_the_self_test_checks_https_can_work():
    assert selftest._tls().startswith("https via ")


def test_the_self_test_times_the_editor_and_a_big_library(monkeypatch):
    monkeypatch.setattr(perf, "open_editor_s", lambda: 0.2)
    monkeypatch.setattr(perf, "big_library",
                        lambda snips: {"open_s": 0.5, "refresh_s": 0.05, "snips": snips, "plain_read_s": 0.1})
    assert selftest._speed().startswith("snip_to_editor_s 0.2, library_open_s 0.5, library_refresh_s 0.05")
    monkeypatch.setattr(perf, "big_library", lambda snips: {"open_s": 30.0, "refresh_s": 0.05, "plain_read_s": 0.1})
    with pytest.raises(RuntimeError, match="library_open_s is 30.0"):
        selftest._speed()


def test_a_slow_disk_moves_the_librarys_budget_not_its_code(monkeypatch):
    """A CI machine's antivirus made reading the same Library 15 times slower: the budget follows the
    machine's plain read, so a slow disk passes and reading what isn't needed still fails."""
    assert perf.library_budgets(0.1) == {"library_open_s": perf.BUDGETS["library_open_s"],
                                         "library_refresh_s": perf.BUDGETS["library_refresh_s"]}
    slow = perf.library_budgets(2.0)
    slower = perf.library_budgets(4.0)
    assert slower["library_open_s"] == 9.0 and slow["library_refresh_s"] == 2.0
    assert perf.over_budget({"library_open_s": 3.6}, slow) == []
    assert perf.over_budget({"library_open_s": 12.0}, slower) == ["library_open_s is 12.0 (budget 9.0)"]


def test_the_plain_read_is_measured(tmp_path, qapp):
    perf.make_library(tmp_path / "Library", 10)
    assert 0 < perf.plain_read_s(tmp_path / "Library") < 5


def test_the_editor_timing_works(qapp):
    assert 0 < perf.open_editor_s(runs=1) < perf.BUDGETS["snip_to_editor_s"]


# --- the Library reads only what changed ------------------------------------------------------------
@pytest.fixture
def snips(qapp, tmp_path):
    root = tmp_path / "Library"
    perf.make_library(root, 120)
    an_hour_ago = time.time() - 3600
    for folder in root.iterdir():
        os.utime(folder, (an_hour_ago, an_hour_ago))
    return Library(root)


def counting(monkeypatch):
    listed = []
    real = library_module._snapshot
    monkeypatch.setattr(library_module, "_snapshot", lambda folder: listed.append(folder.name) or real(folder))
    return listed


def test_the_library_lists_a_snips_folder_again_only_when_it_changed(snips, monkeypatch):
    listed = counting(monkeypatch)
    first = snips.scan()
    assert len(first) == 120 and len(listed) == 120
    listed.clear()
    assert [s.entry.id for s in snips.scan()] == [s.entry.id for s in first] and listed == []
    entry = first[5].entry
    snips.update_meta(entry, favorite=True)  # written beside, then renamed in: the folder's time moves
    again = snips.scan()
    assert listed == [entry.id]
    assert next(s for s in again if s.entry.id == entry.id).meta_stamp != first[5].meta_stamp


def test_a_new_snip_and_a_deleted_one_show_at_once(snips, white_image):
    snips.scan()
    gone = snips.scan()[0].entry
    import shutil
    shutil.rmtree(gone.path)
    made = snips.create(white_image)
    ids = [s.entry.id for s in snips.scan()]
    assert made.id in ids and gone.id not in ids and len(ids) == 120


def test_a_folder_changed_just_now_is_always_listed(snips, monkeypatch):
    snips.scan()
    folder = next(snips.root.iterdir())
    os.utime(folder, None)  # changed now: a coarse clock could hide a second change this second
    listed = counting(monkeypatch)
    snips.scan()
    snips.scan()
    assert listed == [folder.name, folder.name]


def test_a_folder_that_could_not_be_listed_is_tried_again(snips, monkeypatch):
    folder = sorted(snips.root.iterdir())[3]
    real = library_module.os.scandir
    failed = []

    def locked_once(path):
        if str(path) == str(folder) and not failed:
            failed.append(path)
            raise PermissionError(32, "being used by another process")
        return real(path)
    monkeypatch.setattr(library_module.os, "scandir", locked_once)
    assert folder.name not in [s.entry.id for s in snips.scan()]  # locked for a moment (antivirus, a sync)
    assert folder.name in [s.entry.id for s in snips.scan()]  # back as soon as it can be read


def test_a_folder_listed_just_after_it_changed_is_listed_again(snips, monkeypatch):
    """A second change in the same tick of a coarse clock wouldn't move the folder's time, so a
    listing taken within a few seconds of a change isn't trusted later."""
    folder = sorted(snips.root.iterdir())[0]
    os.utime(folder, None)  # changed just now
    snips.scan()
    later = time.time() + 60
    monkeypatch.setattr(library_module.time, "time", lambda: later)  # long after: still not trusted
    listed = counting(monkeypatch)
    snips.scan()
    assert listed == [folder.name]
    listed.clear()
    snips.scan()
    assert listed == []  # this listing came long after the change: trusted from now on


def test_f5_reads_changes_made_outside_snipforge(snips, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from snipforge.home import HomeWindow
    home = HomeWindow(snips, lambda: None, lambda _id: None)
    home.show()
    entry = home._items[0].entry
    folder_time = os.stat(entry.path).st_mtime_ns
    meta = entry.path / "meta.json"
    meta.write_text(json.dumps({"app": "Edited by hand", "tags": []}), encoding="utf-8")  # in place, no rename
    os.utime(entry.path, ns=(folder_time, folder_time))  # the folder's time as it was (a restore, say)
    home.refresh()
    assert next(i for i in home._items if i.entry.id == entry.id).meta.app == "Contoso Analytics"  # not seen
    home.activateWindow()
    assert QTest.qWaitForWindowActive(home)  # window shortcuts need the window in front
    QTest.keyClick(home, Qt.Key_F5)
    assert next(i for i in home._items if i.entry.id == entry.id).meta.app == "Edited by hand"
    home.close()


def test_starring_a_snip_shows_after_the_library_reopens(snips, qapp):
    from snipforge.home import HomeWindow
    home = HomeWindow(snips, lambda: None, lambda _id: None)
    home.show()
    entry = home._items[7].entry
    home.hide()
    snips.update_meta(entry, favorite=True)  # from the editor, say
    home.show()  # opening it again refreshes
    assert next(i for i in home._items if i.entry.id == entry.id).meta.favorite
    home.close()


def test_the_library_window_reads_every_snips_details(snips, qapp):
    from snipforge.home import HomeWindow
    home = HomeWindow(snips, lambda: None, lambda _id: None)
    home.show()
    metas = [item.meta for item in home._items]
    assert len(metas) == 120 and all(m.app == "Contoso Analytics" and m.tags == ("report",) for m in metas)
    home.close()


@pytest.mark.parametrize("entry_id, expected", [
    ("20261018-153012", dt.datetime(2026, 10, 18, 15, 30, 12)),
    ("20261018-153012-123", dt.datetime(2026, 10, 18, 15, 30, 12)),
    ("20261018-153012-123-2", dt.datetime(2026, 10, 18, 15, 30, 12)),
])
def test_a_snips_date_comes_from_its_name(entry_id, expected):
    assert Entry(entry_id, Path("unused")).created == expected


def test_an_oddly_named_snip_uses_its_folder_time(tmp_path):
    folder = tmp_path / "imported-snip"
    folder.mkdir()
    assert abs((Entry(folder.name, folder).created - dt.datetime.now()).total_seconds()) < 60
    bad_date = tmp_path / "20261399-999999"
    bad_date.mkdir()
    assert abs((Entry(bad_date.name, bad_date).created - dt.datetime.now()).total_seconds()) < 60
