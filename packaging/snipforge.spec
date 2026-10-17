# PyInstaller spec for SnipForge (one-folder, windowed). Build with: python scripts/build.py
# On macOS it also wraps the folder in SnipForge.app (python scripts/build_mac.py, docs/MAC-PLAN.md).
# ruff: noqa
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
MAC = sys.platform == "darwin"
VERSION_FILE = os.environ.get("SNIPFORGE_VERSION_FILE", str(ROOT / "build" / "version_info.txt"))
# Never change it once Macs have it: macOS keys the Screen Recording permission and settings to it.
BUNDLE_ID = "io.github.nighthawk909.snipforge"

# Big parts of Qt that SnipForge never uses; leaving them out keeps the download small.
EXCLUDES = [
    "tkinter", "unittest", "pydoc_data",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.QtQuick3D", "PySide6.QtMultimediaWidgets", "PySide6.QtPdf", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtPositioning",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtOpenGL",
]

a = Analysis(
    [str(ROOT / "packaging" / "snipforge_main.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "assets" / "snipforge.ico"), "assets")],  # license files are copied by scripts/build.py
    # Qt Multimedia is imported only when needed (snipforge/video), so it's named here.
    hiddenimports=["PySide6.QtMultimedia"] + ([] if MAC else [
        "winrt.windows.media.ocr", "winrt.windows.graphics.imaging", "winrt.windows.storage.streams",
        "winrt.windows.foundation", "winrt.windows.foundation.collections", "winrt.windows.globalization",
        "win32com.shell", "win32com.client",
    ]),
    excludes=EXCLUDES,
    noarchive=False,
)
# Software OpenGL fallback (~20 MB) isn't needed for a widgets app.
a.binaries = [b for b in a.binaries if not b[0].lower().endswith("opengl32sw.dll")]


def _media_backend_we_use(name):
    """Recording uses Qt Multimedia's FFmpeg back-end only (docs/VIDEO-PLAN.md); its other back-end
    (Media Foundation on Windows, AVFoundation on macOS) stays out."""
    name = name.replace("\\", "/").lower()
    return "/plugins/multimedia/" not in name or "ffmpegmediaplugin" in name


# Installing PySide6-Addons (for Qt Multimedia) also brings Qt plugins that PyInstaller collects on
# its own: PDF images (qpdf, which needs Qt Pdf) and the virtual keyboard (which needs Qt Quick, QML
# and Qt OpenGL). About 20 MB SnipForge never uses; Qt Multimedia itself needs none of them.
UNUSED_QT = ("qpdf", "virtualkeyboard", "qt6pdf", "qtpdf", "qt6quick", "qtquick", "qt6qml", "qtqml",
             "qt6opengl", "qtopengl")


def _used(name):
    name = name.replace("\\", "/").lower()
    return _media_backend_we_use(name) and not any(part in name for part in UNUSED_QT)


a.binaries = [b for b in a.binaries if _used(b[0])]
a.datas = [d for d in a.datas if not os.path.basename(d[0]).lower().startswith("qtmultimedia_")]  # translations
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="SnipForge",
    icon=str(ROOT / "assets" / "snipforge.ico"),  # PyInstaller converts it to .icns on macOS (Pillow)
    version=None if MAC else VERSION_FILE,
    target_arch="universal2" if MAC else None,  # one app for Apple silicon and Intel Macs
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="SnipForge", upx=False)
if MAC:
    VERSION = os.environ.get("SNIPFORGE_VERSION", "0.0.0")
    app = BUNDLE(
        coll,
        name="SnipForge.app",
        icon=str(ROOT / "assets" / "snipforge.ico"),
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist={
            "CFBundleName": "SnipForge",
            "CFBundleDisplayName": "SnipForge",
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            # PySide6 6.12's wheels need macOS 14; older Macs get a clear "needs a newer macOS".
            "LSMinimumSystemVersion": "14.0",
            "LSApplicationCategoryType": "public.app-category.graphics-design",
        },
    )
