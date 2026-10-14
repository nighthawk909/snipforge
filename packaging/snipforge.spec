# PyInstaller spec for SnipForge (one-folder, windowed). Build with: python scripts/build.py
# ruff: noqa
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
VERSION_FILE = os.environ.get("SNIPFORGE_VERSION_FILE", str(ROOT / "build" / "version_info.txt"))

# Big parts of Qt that SnipForge never uses; leaving them out keeps the download small.
EXCLUDES = [
    "tkinter", "unittest", "pydoc_data",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.QtQuick3D", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtPositioning",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtOpenGL",
]

a = Analysis(
    [str(ROOT / "packaging" / "snipforge_main.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "assets" / "snipforge.ico"), "assets"), (str(ROOT / "LICENSE"), "."),
           (str(ROOT / "THIRD_PARTY_NOTICES.md"), ".")],
    hiddenimports=[
        "winrt.windows.media.ocr", "winrt.windows.graphics.imaging", "winrt.windows.storage.streams",
        "winrt.windows.foundation", "winrt.windows.foundation.collections", "winrt.windows.globalization",
        "win32com.shell", "win32com.client",
    ],
    excludes=EXCLUDES,
    noarchive=False,
)
# Software OpenGL fallback (~20 MB) isn't needed for a widgets app.
a.binaries = [b for b in a.binaries if not b[0].lower().endswith("opengl32sw.dll")]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="SnipForge",
    icon=str(ROOT / "assets" / "snipforge.ico"),
    version=VERSION_FILE,
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="SnipForge", upx=False)
