# Third-party software in SnipForge builds

SnipForge's own code is MIT licensed (see `LICENSE`). The Windows builds bundle these components,
each under its own license:

| Component | Used for | License |
|---|---|---|
| [Python](https://www.python.org/) runtime | runs the app | PSF License 2.0 |
| [Qt 6](https://www.qt.io/) via [PySide6 / Shiboken6](https://pypi.org/project/PySide6/) | user interface | LGPL-3.0 |
| [pywin32](https://github.com/mhammond/pywin32) | Outlook draft, Explorer share commands | PSF License 2.0 |
| [pywinrt](https://github.com/pywinrt/pywinrt) (`winrt-*` packages) | Windows built-in text recognition (OCR) | MIT |

## LGPL-3.0 (Qt / PySide6)

Qt and PySide6 are used unmodified and are dynamically linked: they ship as separate DLL and `.pyd`
files in the install folder (`%LOCALAPPDATA%\Programs\SnipForge` by default), and you may replace them
with your own compatible builds. Their source code is available from
[download.qt.io](https://download.qt.io/official_releases/qt/) and
[code.qt.io/pyside](https://code.qt.io/cgit/pyside/pyside-setup.git/). The full LGPL-3.0 text is at
<https://www.gnu.org/licenses/lgpl-3.0.html>.

A machine-readable list of every bundled Python package and version (SBOM, CycloneDX format) is
attached to each GitHub release as `sbom.cdx.json`.
