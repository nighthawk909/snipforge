"""Report a problem: the log and crash files, the report built from them with private details
taken out, the zip it's saved in, and where people find it (tray, About, Settings)."""
import datetime as dt
import json
import logging
import os
import subprocess
import sys
import textwrap
import threading
import zipfile
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from snipforge import diagnostics, logs, report_dialog

HOME = Path(r"C:\Users\jordan.q") if sys.platform == "win32" else Path("/Users/jordan.q")
ENV = {"USERNAME": "jordan.q", "COMPUTERNAME": "CONTOSO-LT42", "USERDOMAIN": "CONTOSO",
       "OneDriveCommercial": str(HOME / "OneDrive - Contoso")}


class FakeFaulthandler:
    def __init__(self):
        self.enabled = None

    def enable(self, file, all_threads=True):
        self.enabled = file

    def disable(self):
        self.enabled = None


@pytest.fixture
def faults(monkeypatch):
    """setup() swaps sys.excepthook, threading.excepthook and faulthandler: put them back after."""
    fake = FakeFaulthandler()
    monkeypatch.setattr(logs, "faulthandler", fake)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    return fake


@pytest.fixture
def session(tmp_path, faults):
    made = []

    def start(name="snipforge"):
        s = logs.setup(name, tmp_path, slots=logs.SLOTS if name == "recorder" else 1)
        made.append(s)
        return s
    yield start
    for s in made:
        s.close()


# --- the log and crash files --------------------------------------------------------------------
def test_the_log_records_what_snipforge_logs_and_errors_nothing_caught(session, tmp_path, faults):
    s = session()
    assert s.files.running.exists() and faults.enabled is not None and not s.crashed_last_time
    logging.getLogger("snipforge.test").info("Hello from the test")
    try:
        raise ValueError("a slot went wrong")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    text = s.files.log.read_text(encoding="utf-8")
    assert "Hello from the test" in text and "ValueError: a slot went wrong" in text
    assert "ValueError: a slot went wrong" in s.files.last_error.read_text(encoding="utf-8")
    s.close()
    assert not s.files.running.exists() and faults.enabled is None
    logging.getLogger("snipforge.test").info("After quitting")
    assert "After quitting" not in s.files.log.read_text(encoding="utf-8")


def test_an_error_in_a_thread_is_kept_too(session):
    s = session()
    worker = threading.Thread(target=lambda: 1 / 0, name="snipforge-worker")
    worker.start()
    worker.join()
    kept = s.files.last_error.read_text(encoding="utf-8")
    assert "snipforge-worker" in kept and "ZeroDivisionError" in kept


def test_crash_details_are_kept_only_when_the_last_run_did_not_quit_normally(session, tmp_path):
    files = logs.Files(tmp_path, "snipforge")
    files.running.write_text("123 2026-10-18 09:00:00\n", encoding="utf-8")  # it never quit (123 may be alive now)
    files.crash.write_text("Windows fatal exception: access violation\n\nCurrent thread 0x1:\n", encoding="utf-8")
    first = session()
    assert first.crashed_last_time
    kept = files.last_crash.read_text(encoding="utf-8")
    assert "access violation" in kept and "Started: 2026-10-18 09:00:00" in kept
    first.close()
    files.crash.write_text("Windows fatal exception: code 0xe0434352\n", encoding="utf-8")  # handled by Windows
    second = session()  # the last run quit normally: that's not a crash
    assert not second.crashed_last_time and "access violation" in files.last_crash.read_text(encoding="utf-8")


@pytest.mark.windows  # a Mac developer would get macOS's "Python quit unexpectedly" window
def test_a_real_crash_is_found_at_the_next_start(session, tmp_path):
    code = textwrap.dedent(f"""
        import faulthandler, sys
        from pathlib import Path
        from snipforge import logs
        logs.setup("snipforge", Path({str(tmp_path)!r}))
        faulthandler._sigsegv()
    """)
    crashed = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=60,
                             cwd=Path(__file__).resolve().parents[1])
    assert crashed.returncode != 0
    assert "Segmentation fault" in logs.Files(tmp_path, "snipforge").unseen_crash()  # before the next start too
    s = session()
    assert s.crashed_last_time
    assert "Fatal Python error: Segmentation fault" in s.files.last_crash.read_text(encoding="utf-8")


def gone_pid():
    finished = subprocess.Popen([sys.executable, "-c", ""])
    finished.wait()
    return finished.pid


def test_a_recorder_that_crashed_is_in_the_report_before_the_next_recording(tmp_path):
    files = logs.Files(tmp_path, "recorder")
    files.running.write_text(f"{gone_pid()} 2026-10-18 09:00:00\n", encoding="utf-8")
    files.crash.write_text("Windows fatal exception: access violation\n", encoding="utf-8")
    assert files.ended_badly() and "access violation" in files.unseen_crash()
    report = diagnostics.build(log_folder=tmp_path, policy_hives=[{}, {}], environ={}, home=HOME, hostnames=())
    assert report.file("recorder.crash.txt").title == "Crash of the recorder"
    assert "Crash of the recorder:" in report.file("summary.txt").text


def test_a_running_process_crash_file_is_not_a_crash(tmp_path):
    files = logs.Files(tmp_path, "recorder")
    files.running.write_text(f"{os.getppid()} 2026-10-18 09:00:00\n", encoding="utf-8")  # alive
    files.crash.write_text("Windows fatal exception: code 0xc0000005\n", encoding="utf-8")
    assert not files.ended_badly() and files.unseen_crash() == ""


def test_snipforge_itself_always_takes_its_own_files(session, tmp_path):
    files = logs.Files(tmp_path, "snipforge")
    files.running.write_text(f"{os.getppid()} 2026-10-18 09:00:00\n", encoding="utf-8")  # a reused process id
    files.crash.write_text("Fatal Python error: Segmentation fault\n", encoding="utf-8")
    s = session()
    assert s.files.name == "snipforge" and s.crashed_last_time  # one copy at a time: that run is over


def test_a_second_recorder_keeps_its_own_files(session, tmp_path):
    busy = logs.Files(tmp_path, "recorder")
    busy.running.write_text(f"{os.getppid()} 2026-10-18 09:00:00\n", encoding="utf-8")  # still finishing
    busy.crash.write_text("not ours", encoding="utf-8")
    second = session("recorder")
    assert second.files.name == "recorder-2" and busy.crash.read_text(encoding="utf-8") == "not ours"
    assert logs.names(tmp_path) == ["recorder", "recorder-2"]
    assert diagnostics.process_label("recorder-2") == "the recorder (2)"
    busy.running.write_text(f"{gone_pid()} 2026-10-18 09:00:00\n", encoding="utf-8")
    second.close()
    assert session("recorder").files.name == "recorder"  # its last user is gone: free again


def test_errors_windows_handled_are_not_kept_as_a_crash(session, monkeypatch):
    s = session()
    s._crash_file.write("Windows fatal exception: code 0xc0000005\n")
    s._crash_file.flush()
    s.tick()
    assert s.files.crash.read_text(encoding="utf-8") == ""
    assert "code 0xc0000005" in s.files.log.read_text(encoding="utf-8")


def test_a_cancelled_sign_out_puts_the_marker_back(session, monkeypatch):
    s = session()
    s.ending()  # Windows asked; another app then cancelled the sign-out
    assert not s.files.running.exists()
    s.tick()
    assert not s.files.running.exists()  # not straight away: Windows may still be ending the session
    monkeypatch.setattr(logs, "REVIVE_AFTER_S", -1)
    s.tick()
    assert s.files.running.exists()


def test_closing_leaves_a_hook_installed_later_alone(session):
    s = session()
    later = lambda *a: None  # noqa: E731 - the recorder installs its own
    sys.excepthook = later
    s.close()
    assert sys.excepthook is later


def test_a_flood_of_errors_is_counted_not_written_out(session):
    s = session()
    logs._recent_errors.clear()
    for n in range(logs.ERRORS_PER_MINUTE + 5):
        try:
            raise RuntimeError(f"paint failed {n}")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
    text = s.files.log.read_text(encoding="utf-8")
    assert text.count("RuntimeError: paint failed") == logs.ERRORS_PER_MINUTE
    logs._recent_errors.clear()
    try:
        raise RuntimeError("after the flood")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    assert "(5 more not written out)" in s.files.log.read_text(encoding="utf-8")
    logs._recent_errors.clear()


def test_the_recorder_keeps_its_last_error(session):
    s = session("recorder")
    try:
        raise ValueError("in the recorder")
    except ValueError:
        logs.remember_error(*sys.exc_info())
    assert "ValueError: in the recorder" in s.files.last_error.read_text(encoding="utf-8")


def test_an_unwritable_folder_means_no_log_not_a_failure(tmp_path, faults):
    blocked = tmp_path / "file"
    blocked.write_text("not a folder", encoding="utf-8")
    assert logs.setup("snipforge", blocked) is None


def test_logs_have_a_home_on_each_platform(monkeypatch, on_a_mac):
    monkeypatch.delenv(logs.FOLDER_VARIABLE)
    if sys.platform == "win32":
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
        assert logs.folder() == Path(r"C:\Users\x\AppData\Local\SnipForge\logs")
    on_a_mac(logs)
    assert logs.folder() == Path.home() / "Library" / "Logs" / "SnipForge"


# --- taking private details out -------------------------------------------------------------------
@pytest.fixture
def scrub():
    return diagnostics.Scrubber(ENV, HOME, hostnames=("CONTOSO-LT42",))


def test_names_paths_emails_and_servers_are_taken_out(scrub):
    text = scrub(
        rf"Couldn't open {HOME}\Pictures\SnipForge\x.png; "
        f"{str(HOME).replace(chr(92), '/').lower()}/AppData/Local/Temp/SnipForge; "
        rf"{HOME}\OneDrive - Contoso\SnipForge\share.png; "
        r"C:\Users\sam.lee\Desktop\notes.txt; "
        r"feed \\fileserver01\apps$\SnipForge; pipe \\.\pipe\SnipForge-recorder-ab12; display \\.\DISPLAY1; "
        "https://intranet.contoso.com/snipforge/releases.json and https://api.github.com/repos/x; "
        "mailed alex.kim@contoso.com; signed in as JORDAN.Q on CONTOSO-LT42 in CONTOSO")
    for private in ("jordan.q", "sam.lee", "contoso", "fileserver01", "apps$", "intranet", "alex.kim"):
        assert private not in text.lower(), private
    assert r"%USERPROFILE%\Pictures\SnipForge\x.png" in text
    assert "%USERPROFILE%/AppData/Local/Temp/SnipForge" in text
    assert r"%OneDrive%\SnipForge\share.png" in text
    assert r"\\<server>\<share>\SnipForge" in text
    assert r"\\.\pipe\SnipForge-recorder-ab12" in text and r"\\.\DISPLAY1" in text
    assert "https://<host>/snipforge" in text and "https://api.github.com/repos/x" in text
    assert "<email>" in text and "<user> on <computer> in <domain>" in text


@pytest.mark.parametrize("written", [
    r"\\fileserver01\apps\x.png",
    "'\\\\\\\\fileserver01\\\\apps\\\\x.png'",  # as Python shows a path in an error message
    "//fileserver01/apps/x.png",
    r"\\?\UNC\fileserver01\apps\x.png",
])
def test_a_network_share_is_taken_out_however_it_is_written(scrub, written):
    text = scrub(f"PermissionError: [Errno 13] Permission denied: {written}")
    assert "fileserver01" not in text and "apps" not in text and "<server>" in text and "x.png" in text


def test_server_names_and_the_organization_domain_are_taken_out():
    scrub = diagnostics.Scrubber({"USERDNSDOMAIN": "CORP.FABRIKAM.COM", "USERNAME": "sam"}, HOME,
                                 hosts=["updates.example-feed.net", "feedserver"], hostnames=("Sams-MacBook.local",))
    text = scrub("Host fs01.corp.fabrikam.com not found; came from updates.example-feed.net; feedserver timed out; "
                 "fabrikam staff; on Sams-MacBook.local and Sams-MacBook; logger snipforge.update_sources; x.png; "
                 "objects.githubusercontent.com is fine")
    for private in ("fabrikam", "example-feed", "feedserver", "Sams-MacBook", "fs01"):
        assert private.lower() not in text.lower(), private
    assert "snipforge.update_sources" in text and "x.png" in text
    assert text.split("; ")[-1] == "objects.githubusercontent.com is fine"  # GitHub's own host stays


def test_onedrive_folders_of_any_organization_are_taken_out(scrub):
    text = scrub(rf"Saved to {HOME}\OneDrive - Contoso Ltd. and D:\Data\OneDrive - Fabrikam Inc\x.png")
    assert "Contoso" not in text and "Fabrikam" not in text and r"OneDrive - <organization>\x.png" in text


def test_the_policy_feed_names_its_server():
    assert diagnostics.policy_hosts([{"UpdateFeed": "https://feed.contoso.com/snipforge"},
                                     {"UpdateFeed": r"\\fileserver01\apps\SnipForge"}, {}]) == \
        ["feed.contoso.com", "fileserver01"]


def test_a_name_is_only_taken_out_as_a_whole_word():
    scrub = diagnostics.Scrubber({"USERNAME": "al"}, Path("/Users/al"), hostnames=())
    assert scrub("calendar al /Users/al/x /Users/alice/y") == "calendar <user> %USERPROFILE%/x /Users/<user>/y"


@pytest.mark.windows
def test_the_windows_version_comes_from_the_registry():
    from snipforge._diagnostics_win32 import windows_version
    assert windows_version().startswith("Windows") and "build" in windows_version()


# --- the parts of the report ----------------------------------------------------------------------
def settings_store(tmp_path, values):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    for key, value in values.items():
        store.setValue(key, value)
    return store


def test_settings_keep_choices_and_leave_out_anything_personal(tmp_path):
    store = settings_store(tmp_path, {
        "appearance/theme": "dark", "capture/delay": 3, "capture/magnifier": True, "shortcuts/snip": "Ctrl+Alt+S",
        "teams_recent": ["alex.kim@contoso.com"], "output/folder": str(HOME / "Documents"),
        "capture/last_area": '{"screen": "DELL U2720Q"}', "video/microphone_id": "{0.0.1.00000000}.{abc}",
        "tool_styles": json.dumps({"arrow": {"color": "#e11d48", "width": 4}} | {f"t{n}": n for n in range(20)})})
    hives = [{"UpdateFeed": r"\\fileserver01\apps\SnipForge", "UpdateMode": "auto"}, {"DisableUpdateCheck": 1}]
    text = diagnostics.settings_text(store, hives)
    for shown in ("appearance/theme = dark", "capture/delay = 3", "capture/magnifier = true",
                  "shortcuts/snip = Ctrl+Alt+S", "Machine: UpdateMode = auto", "User: DisableUpdateCheck = 1",
                  "Machine: UpdateFeed = (a network share; left out)"):
        assert shown in text, shown
    for hidden in ("alex.kim", "Documents", "DELL", "00000000", "fileserver01", "#e11d48"):
        assert hidden not in text, hidden
    assert "teams_recent = " + diagnostics.LEFT_OUT in text
    assert "tool_styles = (left out:" in text


def test_no_policy_says_so(tmp_path):
    assert "(none set)" in diagnostics.settings_text(settings_store(tmp_path, {}), [{}, {}])
    assert "(not read on this platform)" in diagnostics.settings_text(settings_store(tmp_path, {}), [])


def test_only_the_newest_end_of_a_long_log_goes_in(tmp_path):
    files = logs.Files(tmp_path, "snipforge")
    Path(f"{files.log}.1").write_text("".join(f"old line {n}\n" for n in range(5000)), encoding="utf-8")
    files.log.write_text("".join(f"new line {n}\n" for n in range(100)), encoding="utf-8")
    text = diagnostics.log_text(files, limit=2000)
    assert text.endswith("new line 99\n") and "new line 0\n" in text and "old line 0\n" not in text
    assert text.split("\n")[0].startswith(("old line", "new line"))  # starts on a whole line


# --- the whole report ------------------------------------------------------------------------------
@pytest.fixture
def library_with_a_private_snip(tmp_path, white_image):
    from snipforge.library import Library
    from snipforge.winrects import WindowInfo

    library = Library(tmp_path / "Library")
    from PySide6.QtCore import QRect
    library.create(white_image, source=WindowInfo(QRect(0, 0, 10, 10), "Quarterly salaries - Excel", "Excel"))
    return library


def test_the_report_has_versions_displays_settings_logs_and_the_last_crash(tmp_path, qapp,
                                                                          library_with_a_private_snip):
    folder = tmp_path / "logs"
    files = logs.Files(folder, "snipforge")
    folder.mkdir()
    files.log.write_text(f"INFO snipforge.app: Couldn't open {HOME}\\Pictures\\x.png\n", encoding="utf-8")
    files.last_crash.write_text(f"Windows fatal exception: access violation\n  File \"{HOME}\\app.py\"\n",
                                encoding="utf-8")
    report = diagnostics.build(log_folder=folder, library_root=library_with_a_private_snip.root,
                               settings=settings_store(tmp_path, {"appearance/theme": "dark"}), policy_hives=[{}, {}],
                               environ=ENV, home=HOME, hostnames=(), now=dt.datetime(2026, 10, 18, 15, 30))
    names = [f.name for f in report.files]
    assert names == ["summary.txt", "settings.txt", "snipforge.log", "snipforge.last-crash.txt"]
    assert report.default_name == "SnipForge-report-2026-10-18-1530.zip"
    summary = report.file("summary.txt").text
    from snipforge import __version__
    for expected in (f"SnipForge {__version__}", "Python 3.", "Qt 6.", "Operating system:", "Displays",
                     "% scaling", "1 snips and recordings", "Last crash of SnipForge"):
        assert expected in summary, expected
    everything = "\n".join(f.text for f in report.files)
    assert "jordan.q" not in everything and "%USERPROFILE%" in everything
    assert "Quarterly salaries" not in everything  # the Library's window titles are never read


def test_saving_writes_a_zip_with_every_part(tmp_path):
    report = diagnostics.Report(dt.datetime(2026, 10, 18, 15, 30), (
        diagnostics.ReportFile("summary.txt", "Summary", "SnipForge 1.0\n"),
        diagnostics.ReportFile("snipforge.log", "Log", "line\n")))
    path = diagnostics.save(report, tmp_path / report.default_name)
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == ["summary.txt", "snipforge.log"]
        assert all(info.external_attr >> 16 == 0o644 for info in archive.infolist())  # readable once unzipped
        assert archive.read("summary.txt").decode() == "SnipForge 1.0\n"
    assert [p.name for p in tmp_path.iterdir()] == [report.default_name]  # no half-written file left


def test_a_failed_save_leaves_nothing_behind(tmp_path, monkeypatch):
    report = diagnostics.Report(dt.datetime(2026, 10, 18), (diagnostics.ReportFile("a.txt", "A", "x"),))
    monkeypatch.setattr(diagnostics.os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("in use")))
    with pytest.raises(PermissionError):
        diagnostics.save(report, tmp_path / "r.zip")
    assert list(tmp_path.iterdir()) == []


# --- the window ------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def no_report_left_open():
    yield
    report_dialog._open.clear()


@pytest.fixture
def dialog(qapp):
    report = diagnostics.Report(dt.datetime(2026, 10, 18, 15, 30), (
        diagnostics.ReportFile("summary.txt", "Summary: versions and displays", "SnipForge 0.11.1\nDisplays\n"),
        diagnostics.ReportFile("snipforge.log", "Recent log of SnipForge", "INFO hello\n")))
    d = report_dialog.ReportDialog(report)
    d.show()
    yield d
    d.close()


def click_row(listing, row):
    rect = listing.visualItemRect(listing.item(row))
    QTest.mouseClick(listing.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())


def test_the_window_shows_each_part_before_anything_is_saved(dialog):
    assert dialog.contents.count() == 2 and "SnipForge 0.11.1" in dialog.preview.toPlainText()
    click_row(dialog.contents, 1)
    assert dialog.preview.toPlainText() == "INFO hello\n" and dialog.preview.isReadOnly()
    assert not dialog.status.isVisible() and not dialog.show_folder.isVisible()


def test_save_asks_where_then_says_where_it_went(dialog, tmp_path, monkeypatch):
    asked = []

    def ask(parent, title, start, filters):
        asked.append(start)
        return str(tmp_path / "my report"), filters
    monkeypatch.setattr(QFileDialog, "getSaveFileName", ask)
    QTest.mouseClick(dialog.save_button, Qt.LeftButton)
    assert asked[0].endswith("SnipForge-report-2026-10-18-1530.zip")
    saved = tmp_path / "my report.zip"
    assert saved.is_file() and dialog.saved_to == saved
    assert str(saved) in dialog.status.text() and dialog.show_folder.isVisible()


def test_saving_somewhere_it_cant_says_so(dialog, tmp_path):
    assert not dialog.save_to(tmp_path / "missing folder" / "r.zip")
    assert dialog.status.isVisible() and "Couldn't save" in dialog.status.text() and dialog.saved_to is None


def test_cancelling_the_save_question_saves_nothing(dialog, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: ("", ""))
    dialog.choose_and_save()
    assert dialog.saved_to is None and not dialog.status.isVisible()


def test_typing_a_name_without_zip_never_replaces_a_file_unasked(dialog, tmp_path, monkeypatch):
    (tmp_path / "report.zip").write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    assert not dialog.save_to(tmp_path / "report")
    assert (tmp_path / "report.zip").read_text(encoding="utf-8") == "keep me"


def test_a_report_that_cannot_be_put_together_says_so(qapp, monkeypatch, _never_block_on_dialogs):
    monkeypatch.setattr(report_dialog.diagnostics, "build", lambda **k: 1 / 0)
    assert report_dialog.open_report() is None and _never_block_on_dialogs == ["warning"]


def test_one_report_window_at_a_time(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(report_dialog.diagnostics, "build", lambda **k: diagnostics.Report(
        dt.datetime(2026, 10, 18), (diagnostics.ReportFile("summary.txt", "Summary", "x"),)))
    first = report_dialog.open_report(library_root=tmp_path)
    assert report_dialog.open_report() is first
    first.close()
    QApplication.processEvents()
    assert report_dialog._open == []


# --- where to find it ------------------------------------------------------------------------------
@pytest.fixture
def opened(monkeypatch):
    calls = []
    monkeypatch.setattr(report_dialog, "open_report", lambda parent=None, library_root=None: calls.append(library_root))
    return calls


@pytest.fixture
def forge(qapp, tmp_path, monkeypatch):
    from snipforge import app as app_module
    from snipforge.library import Library
    monkeypatch.setattr(app_module.HotkeyListener, "start", lambda self: True)
    monkeypatch.setattr(app_module.HotkeyListener, "stop", lambda self: None)
    monkeypatch.setattr(app_module.UpdateFlow, "start", lambda self: None)
    f = app_module.SnipForgeApp(qapp, Library(tmp_path))
    yield f
    f.indexer.close()


def test_the_tray_menu_has_report_a_problem(forge, opened):
    action = next(a for a in forge._menu.actions() if a.text() == "Report a problem…")
    action.trigger()
    assert opened == [forge.library.root]


def test_about_offers_report_a_problem(forge, opened, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    def click_report(box):
        box.button = next(b for b in box.buttons() if b.text() == "Report a problem…")
        box.button.click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", click_report)
    forge.about()
    assert opened == [forge.library.root]


def test_the_crash_notice_waits_for_an_update_notice(forge, opened, monkeypatch):
    shown, later = [], []
    monkeypatch.setattr(forge._tray, "showMessage", lambda *a: shown.append(a[1]))
    monkeypatch.setattr(forge._updates, "_whats_new", object())  # "SnipForge was updated" is up
    from snipforge import app as app_module
    monkeypatch.setattr(app_module.QTimer, "singleShot", lambda ms, fn: later.append(ms))
    forge.notice_crash()
    assert shown == [] and later == [app_module.CRASH_NOTICE_MS + 1000]
    forge._tray.messageClicked.emit()
    assert opened == []


def test_clicking_the_crash_notice_opens_the_report(forge, opened, monkeypatch):
    shown = []
    monkeypatch.setattr(forge._tray, "showMessage", lambda *a: shown.append(a[1]))
    forge.notice_crash()
    assert "closed unexpectedly" in shown[0]
    forge._tray.messageClicked.emit()
    assert opened == [forge.library.root]
    forge._tray.messageClicked.emit()  # a later notice (an update, say) is that notice's business
    assert len(opened) == 1


def test_settings_about_opens_the_report(qapp, tmp_path, opened):
    from PySide6.QtWidgets import QPushButton

    from snipforge.settings_window import SettingsWindow
    window = SettingsWindow(library_root=tmp_path)
    window.show_section("about")
    button = next(b for b in window.findChildren(QPushButton) if b.text() == "Report a problem")
    QTest.mouseClick(button, Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
    assert opened == [tmp_path]
    window.close()
