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
    assert perf.over_budget(measured) == []


def test_over_budget_names_what_is_over():
    assert perf.over_budget({"startup_s": 0.2, "library_open_s": 9.0, "unbudgeted": 99}) == \
        [f"library_open_s is 9.0 (budget {perf.BUDGETS['library_open_s']})"]


def test_memory_is_measured_on_every_platform(on_a_mac):
    assert 5 < perf.memory_mb() < 4000
    on_a_mac(perf)
    if sys.platform != "darwin":
        pytest.skip("macOS's resource module")


# --- the packaged self-test's budgets ------------------------------------------------------------
def test_the_self_test_fails_when_start_up_loaded_too_much(monkeypatch):
    monkeypatch.setattr(perf, "heavy_modules", lambda: {"snipforge.editor.window"})
    with pytest.raises(RuntimeError, match="loaded at start: snipforge.editor.window"):
        selftest._startup_imports()
    monkeypatch.setattr(perf, "heavy_modules", lambda: set())
    monkeypatch.setattr(perf, "module_budget", lambda: 10)
    with pytest.raises(RuntimeError, match="modules at start"):
        selftest._startup_imports()
    monkeypatch.setattr(perf, "module_budget", lambda: None)  # a Mac: only the heavy ones checked
    assert "none of the heavy ones" in selftest._startup_imports()


def test_the_self_test_times_the_editor_and_a_big_library(monkeypatch):
    monkeypatch.setattr(perf, "open_editor_s", lambda: 0.2)
    monkeypatch.setattr(perf, "big_library", lambda snips: {"open_s": 0.5, "refresh_s": 0.05, "snips": snips})
    assert selftest._speed() == "snip_to_editor_s 0.2, library_open_s 0.5, library_refresh_s 0.05"
    monkeypatch.setattr(perf, "big_library", lambda snips: {"open_s": 30.0, "refresh_s": 0.05})
    with pytest.raises(RuntimeError, match="library_open_s is 30.0"):
        selftest._speed()


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


def test_a_new_snip_and_a_deleted_one_show_at_once(snips, monkeypatch, white_image):
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
