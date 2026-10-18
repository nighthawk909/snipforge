# Third-party software in SnipForge builds

SnipForge's own code is MIT licensed (`LICENSE.txt`). The Windows and Mac builds bundle the
components below, each under its own license (pywin32 and pywinrt only in the Windows build, PyObjC
only in the Mac build). License texts are in the `licenses` folder next to `SnipForge.exe` (default
install folder: `%LOCALAPPDATA%\Programs\SnipForge`), or in `SnipForge.app/Contents/Resources` on a
Mac.

| Component | Version | Used for | License |
|---|---|---|---|
| [Python](https://www.python.org/) runtime | 3.14 | runs the app | PSF License 2.0 |
| [Qt](https://www.qt.io/) libraries (Qt6Core, Qt6Gui, Qt6Widgets, Qt6Svg, Qt6Network, Qt6Multimedia and their plugins) | 6.12.0 | user interface; video encoding and playback | LGPL-3.0 |
| [PySide6 / Shiboken6](https://pypi.org/project/PySide6-Essentials/6.12.0/) ([PySide6-Essentials](https://pypi.org/project/PySide6-Essentials/6.12.0/), [PySide6-Addons](https://pypi.org/project/PySide6-Addons/6.12.0/)) | 6.12.0 | Python bindings for Qt | LGPL-3.0 |
| [FFmpeg](https://ffmpeg.org/) libraries (avcodec, avformat, avutil, swscale, swresample), as built by the Qt Project for Qt Multimedia | 9.0.1 | reading and writing video files | LGPL-2.1-or-later (`licenses/LGPL-2.1.txt`) |
| [Pillow](https://python-pillow.github.io/) (its core and GIF writer only) | 12.3.0 | writing GIFs in the video editor | MIT-CMU (HPND) (`licenses/Pillow-MIT-CMU.txt`) |
| [pywin32](https://github.com/mhammond/pywin32) | 312 | Outlook draft, Explorer share commands | PSF License 2.0 |
| [pywinrt](https://github.com/pywinrt/pywinrt) (`winrt-*` packages) | 3.2.1 | Windows built-in text recognition (OCR) | MIT |
| [PyObjC](https://github.com/ronaldoussoren/pyobjc) (`pyobjc-core`, `pyobjc-framework-Cocoa`, `pyobjc-framework-Quartz`), Mac build only | 12.2.2 | macOS's own APIs (the Screen Recording permission check) | MIT (`licenses/PyObjC-MIT.txt`) |
| [Lucide](https://lucide.dev/) icons (path data embedded in `snipforge/icons.py`) | 1.49.0 | toolbar and menu icons | ISC (`licenses/Lucide-ISC.txt`) |
| [PyInstaller](https://pyinstaller.org/) bootloader | 6.22.3 | starts `SnipForge.exe` | GPL-2.0-or-later with the PyInstaller bootloader exception (allows distribution with any program) |

## LGPL-3.0 (Qt and PySide6)

Qt and PySide6 are used **unmodified** and are **dynamically linked**: they ship as separate
`Qt6*.dll`, `PySide6\*.pyd` and `shiboken6\*` files in the install folder, and you may replace them
with your own compatible builds of the same versions. Corresponding source code:

- Qt 6.12.0: <https://download.qt.io/official_releases/qt/6.12/6.12.0/single/>
- PySide6 / Shiboken6 6.12.0: <https://download.qt.io/official_releases/QtForPython/pyside6/> and
  <https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.12.0>

Full texts: `licenses/LGPL-3.0.txt` and `licenses/GPL-3.0.txt` (the LGPL builds on the GPL).

## LGPL-2.1 (FFmpeg)

The FFmpeg libraries come with Qt Multimedia (the PySide6-Addons package) and are used
**unmodified** and **dynamically linked**: they ship as separate files in `_internal\PySide6`
(`avcodec-63.dll`, `avformat-63.dll`, `avutil-61.dll`, `swscale-10.dll`, `swresample-7.dll`), which
you may replace with your own compatible builds of the same version. They are the Qt Project's build
of FFmpeg 9.0.1 under the LGPL, with no GPL or non-free parts (the libraries report "LGPL version 2.1
or later"), configured with `--disable-programs --disable-doc --disable-debug --enable-network
--disable-lzma --enable-pic --disable-vulkan --disable-v4l2-m2m --disable-decoder=truemotion1
--disable-avdevice --disable-avfilter --enable-zlib --toolchain=msvc --enable-shared --disable-static`
and built with zlib 1.3.1 (zlib license). SnipForge only ever opens local files with them.
Corresponding source code:

- FFmpeg 9.0.1: <https://ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz> (git tag `n9.0.1` at
  <https://git.ffmpeg.org/ffmpeg.git>)

Full text: `licenses/LGPL-2.1.txt`. H.264 video is encoded by Windows' own Media Foundation encoder,
which is part of Windows, not of these libraries.

## MIT-CMU (Pillow)

Pillow is the Python Imaging Library fork (Copyright © 1997-2011 by Secret Labs AB, Copyright ©
1995-2011 by Fredrik Lundh and contributors, Copyright © 2010 by Jeffrey A. Clark and contributors).
Only its core (`PIL\_imaging`, `PIL\_imagingmath`) and Python modules ship; its optional font, colour
management, WebP and AVIF modules are left out of the build. Its core is built with libjpeg-turbo,
libtiff, OpenJPEG, libwebp, xz and zlib-ng, all under permissive licenses. Full texts, as Pillow
12.3.0 ships them (including those of libraries the build leaves out): `licenses/Pillow-MIT-CMU.txt`.

## Bill of materials

Each GitHub release includes `sbom.cdx.json` (CycloneDX), generated from a clean environment
containing only SnipForge's runtime dependencies.
