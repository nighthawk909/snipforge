import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QSettings
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from snipforge.settings import app_settings

# tests/desktop drives real Windows features (and imports winreg), so it only runs on Windows.
collect_ignore = [] if sys.platform == "win32" else ["desktop"]


def pytest_collection_modifyitems(config, items):
    """Tests marked `windows` need Windows itself (registry, Explorer, Win32): skipped elsewhere,
    e.g. in the macOS build workflow."""
    if sys.platform == "win32":
        return
    skip = pytest.mark.skip(reason="needs Windows (docs/MAC-PLAN.md)")
    for item in items:
        if item.get_closest_marker("windows"):
            item.add_marker(skip)


class _MacSys:
    """`sys` as a module sees it on a Mac; everything but `platform` is the real one."""
    platform = "darwin"

    def __getattr__(self, name):
        return getattr(sys, name)


@pytest.fixture
def on_a_mac(monkeypatch):
    """Call it with the modules whose platform checks should see a Mac. Only theirs: pytest itself
    has to keep seeing the real platform."""
    def pretend(*modules):
        for module in modules:
            monkeypatch.setattr(module, "sys", _MacSys())
    return pretend


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


@pytest.fixture(autouse=True)
def _delete_leftover_windows():
    """Windows a test opened are deleted when it ends, not just closed: left alive, every later
    app-wide restyle (theme tests, the tool rail) re-polishes thousands of stale widgets."""
    yield
    if QApplication.instance() is None:
        return
    for window in QApplication.topLevelWidgets():
        if window.isVisible():
            window.close()  # once: the test may have closed it already
        window.deleteLater()
        # Only these windows: other objects waiting to be deleted (a worker thread still finishing,
        # say) must go when their own code is done with them, or Qt aborts.
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
