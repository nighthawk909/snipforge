import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import shiboken6
from PySide6.QtCore import QEvent, QSettings
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from snipforge.settings import app_settings

# tests/desktop drives real Windows features (and imports winreg), so it only runs on Windows.
collect_ignore = [] if sys.platform == "win32" else ["desktop"]


def pytest_collection_modifyitems(config, items):
    """Tests marked `windows` need Windows itself (registry, Explorer, Win32): they're skipped elsewhere."""
    if sys.platform == "win32":
        return
    for item in items:
        if item.get_closest_marker("windows"):
            item.add_marker(pytest.mark.skip(reason="needs Windows"))


class _OtherSys:
    """`sys` as a module sees it off Windows (on Linux); everything but `platform` is the real one."""
    platform = "linux"

    def __getattr__(self, name):
        return getattr(sys, name)


@pytest.fixture
def off_windows(monkeypatch):
    """Call it with the modules whose platform checks should see another system than Windows, to
    prove their Windows-only parts stand aside there. Only theirs: pytest itself has to keep seeing
    the real platform."""
    def pretend(*modules):
        for module in modules:
            monkeypatch.setattr(module, "sys", _OtherSys())
    return pretend


# Log and crash files (snipforge.logs) go to a throwaway folder too, also from processes tests start.
os.environ["SNIPFORGE_LOG_FOLDER"] = tempfile.mkdtemp(prefix="snipforge-test-logs-")

# Keep tests away from the real app's settings (registry): use a throwaway INI file instead.
_SETTINGS_DIR = tempfile.mkdtemp(prefix="snipforge-test-settings-")
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _SETTINGS_DIR)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")  # what theme.install() uses in the real app
    return app


@pytest.fixture(autouse=True, scope="session")
def _isolated_settings():
    probe = app_settings()
    assert os.path.normcase(os.path.normpath(probe.fileName())).startswith(
        os.path.normcase(os.path.normpath(_SETTINGS_DIR))), "tests must never touch real settings"
    yield


@pytest.fixture(autouse=True, scope="session")
def _clipboard_let_go_before_python_exits():
    """Offscreen (and minimal), Qt keeps what's on the clipboard in a C++ static that's freed only
    after Python has shut down. Something put there from Python (the QMimeData Copy makes for a
    recording) then reaches into the dead interpreter as it's freed: an access violation on exit
    (139 in bash, 0xC0000005 natively) whenever the file that put it there runs last. On Windows the
    clipboard is freed with the QApplication, while Python is still there, so the app itself is fine
    (tests/desktop checks)."""
    yield
    if QApplication.instance() is not None and QGuiApplication.platformName() in ("offscreen", "minimal"):
        QGuiApplication.clipboard().clear()


@pytest.fixture(autouse=True)
def _delete_leftover_windows():
    """Windows a test opened are deleted when it ends, not just closed: left alive, every later
    app-wide restyle (theme tests, the tool rail) re-polishes thousands of stale widgets."""
    yield
    if QApplication.instance() is None:
        return
    windows = QApplication.topLevelWidgets()
    for window in windows:
        if shiboken6.isValid(window):  # closing one window can delete others (its menus, say)
            if window.isVisible():
                window.close()  # once: the test may have closed it already
            window.deleteLater()
    # Only these windows: other objects waiting to be deleted (a worker thread still finishing, say)
    # must go when their own code is done with them, or Qt aborts.
    for window in windows:
        if shiboken6.isValid(window):
            QApplication.sendPostedEvents(window, QEvent.DeferredDelete)


@pytest.fixture
def white_image(qapp):
    img = QImage(200, 100, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    return img


@pytest.fixture
def gradient_image(qapp):
    img = QImage(100, 100, QImage.Format_ARGB32)
    for x in range(100):
        for y in range(100):
            img.setPixelColor(x, y, QColor(x * 2, y * 2, 0))
    return img


@pytest.fixture
def no_dialogs(monkeypatch):
    """Record message boxes instead of blocking on them."""
    shown = []
    for name in ("information", "warning", "about"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, n=name, **k: shown.append((n, a)))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: shown.append(("question", a)) or QMessageBox.No)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(("question", self.text())) or QMessageBox.No)
    return shown


@pytest.fixture(autouse=True)
def _never_block_on_dialogs(monkeypatch):
    """A message box a test didn't expect answers No/Cancel instead of hanging the whole run.
    Tests that care about a dialog patch it themselves (see no_dialogs)."""
    opened = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: opened.append(self.text()) or QMessageBox.Cancel)
    for name in ("information", "warning", "critical", "about"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, n=name, **k: opened.append(n) or QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: opened.append("question") or QMessageBox.No)
    return opened


@pytest.fixture(autouse=True)
def _no_update_network(monkeypatch):
    """Tests never reach GitHub or an update feed over the network: a request is answered with an
    error, and the test that made it fails (usually a leaked, still-running update controller)."""
    from urllib.parse import urlparse

    from PySide6.QtCore import QTimer

    from snipforge import update_transport

    calls = []
    real = update_transport.NetworkTransport.get

    def guarded(self, url, **kwargs):
        if urlparse(url or "").scheme != "https":
            return real(self, url, **kwargs)
        calls.append(url)
        QTimer.singleShot(0, lambda: kwargs["done"](None, "The network is off in tests.", url))
        return update_transport._NoTransfer()

    monkeypatch.setattr(update_transport.NetworkTransport, "get", guarded)
    yield
    assert not calls, f"a test tried to reach the network: {calls}"
