# Third-party software in SnipForge builds

SnipForge's own code is MIT licensed (`LICENSE.txt`). The Windows builds bundle the components
below, each under its own license. License texts are in the `licenses` folder next to
`SnipForge.exe` (default install folder: `%LOCALAPPDATA%\Programs\SnipForge`).

| Component | Version | Used for | License |
|---|---|---|---|
| [Python](https://www.python.org/) runtime | 3.14 | runs the app | PSF License 2.0 |
| [Qt](https://www.qt.io/) libraries (Qt6Core, Qt6Gui, Qt6Widgets, Qt6Svg, Qt6Network and their plugins) | 6.12.0 | user interface | LGPL-3.0 |
| [PySide6 / Shiboken6](https://pypi.org/project/PySide6-Essentials/6.12.0/) | 6.12.0 | Python bindings for Qt | LGPL-3.0 |
| [pywin32](https://github.com/mhammond/pywin32) | 312 | Outlook draft, Explorer share commands | PSF License 2.0 |
| [pywinrt](https://github.com/pywinrt/pywinrt) (`winrt-*` packages) | 3.2.1 | Windows built-in text recognition (OCR) | MIT |
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

## Bill of materials

Each GitHub release includes `sbom.cdx.json` (CycloneDX), generated from a clean environment
containing only SnipForge's runtime dependencies.
