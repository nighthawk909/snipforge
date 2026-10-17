import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from snipforge.settings import app_settings

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
