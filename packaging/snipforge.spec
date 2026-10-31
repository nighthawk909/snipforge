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
    "PySide6.QtQuick3D", "PySide6.QtMultimediaWidgets", "PySide6.QtPdf", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtPositioning",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtOpenGL",
    # Pillow only writes GIFs (snipforge/video/gif.py): its core (_imaging, plus _imagingmath, which
    # the GIF writer imports) is enough. Its AVIF, font, WebP, colour management, morphology and Tk
    # modules (~11 MB uncompressed) stay out; Pillow's image plugins carry on without them.
    "PIL._avif", "PIL._imagingft", "PIL._webp", "PIL._imagingcms", "PIL._imagingmorph", "PIL._imagingtk",
    "PIL.ImageTk", "PIL.ImageQt",
    # Pillow names numpy for type checking only; where numpy happens to be installed, PyInstaller
    # would otherwise bundle it (~27 MB, and the YAML and charset modules it brings).
    "numpy",
    # Python's own TLS and OpenSSL hashes (~6 MB with their OpenSSL): SnipForge goes online only
    # through Qt's network stack (QNetworkAccessManager), whose TLS back-ends are separate (OpenSSL
    # where the build finds it, else Windows' Schannel; the self-test's
    # "tls" check proves one works); hashlib falls back to Python's built-in SHA-256, and asyncio
    # (Windows OCR) runs without ssl. tests/test_packaging.py keeps it that way.
    "ssl", "_ssl", "_hashlib",
    # zstd archives: zipfile (Report a problem) only writes deflate.
    "compression.zstd", "_zstd",
]

a = Analysis(
    [str(ROOT / "packaging" / "snipforge_main.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "assets" / "snipforge.ico"), "assets")],  # license files are copied by scripts/build.py
    # Qt Multimedia is imported only when needed (snipforge/video), so it's named here, as are the
    # Windows modules that are imported only when needed (OCR, the shell and COM).
    hiddenimports=[
        "PySide6.QtMultimedia",
        "winrt.windows.media.ocr", "winrt.windows.graphics.imaging", "winrt.windows.storage.streams",
        "winrt.windows.foundation", "winrt.windows.foundation.collections", "winrt.windows.globalization",
        "win32com.shell", "win32com.client",
    ],
    excludes=EXCLUDES,
    noarchive=False,
)
# Software OpenGL fallback (~20 MB) isn't needed for a widgets app.
a.binaries = [b for b in a.binaries if not b[0].lower().endswith("opengl32sw.dll")]


def _media_backend_we_use(name):
    """Recording uses Qt Multimedia's FFmpeg back-end only (docs/VIDEO-PLAN.md); its other back-end
    (Media Foundation) stays out."""
    name = name.replace("\\", "/").lower()
    return "/plugins/multimedia/" not in name or "ffmpegmediaplugin" in name


# Installing PySide6-Addons (for Qt Multimedia) also brings Qt plugins that PyInstaller collects on
# its own: PDF images (qpdf, which needs Qt Pdf) and the virtual keyboard (which needs Qt Quick, QML
# and Qt OpenGL). About 20 MB SnipForge never uses; Qt Multimedia itself needs none of them.
UNUSED_QT = ("qpdf", "virtualkeyboard", "qt6pdf", "qtpdf", "qt6quick", "qtquick", "qt6qml", "qtqml",
             "qt6opengl", "qtopengl",
             # Display plugins other than Windows' own and offscreen (CI's self-test), and touch over TUIO
             "/platforms/qdirect2d", "/platforms/qminimal", "qtuiotouchplugin")


def _used(name):
    name = name.replace("\\", "/").lower()
    return _media_backend_we_use(name) and not any(part in name for part in UNUSED_QT)


a.binaries = [b for b in a.binaries if _used(b[0])]
# Qt's translations of its own dialogs (~7 MB): SnipForge's windows are in English and it never
# loads a translator, so none of them is ever used.
a.datas = [d for d in a.datas if not d[0].lower().endswith(".qm")]
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
