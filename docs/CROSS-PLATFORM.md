# Building for Windows and macOS

SnipForge ships for Windows and macOS from one codebase, and the Mac version is kept at parity with
the Windows one. This page is the working agreement for that: read it before changing anything
that touches the operating system, and before adding a feature. Where each feature stands on each
platform is in [PARITY.md](PARITY.md); the macOS APIs, permissions and Gatekeeper details are in
[MAC-PLAN.md](MAC-PLAN.md).

## The rules

0. **Windows first, then the Mac port** (the maintainer's call; [ROADMAP.md](ROADMAP.md)). Until
   the Windows roadmap is done, new features are built for Windows and step aside on the Mac (rule
   1); then every Windows feature is ported in one go. Meanwhile the Mac version must keep building,
   installing and starting in CI, and its shared features (editor, Library, Settings) keep working.
1. **Every feature ships on both platforms in the same release** (once the Mac port is done; until
   then, see rule 0). If the Mac half can't be built in the same PR, the PR:
   - makes the feature step aside cleanly on the other platform: hidden or disabled with a short
     explanation, never an error or a crash;
   - adds or updates its row in [PARITY.md](PARITY.md) with what's missing and the plan;
   - says so in the PR description, under "Platforms".
2. **Every release carries both builds:** the Windows installer and portable zip, and the Mac
   `.dmg`, all listed in `SHA256SUMS.txt`. The download page offers the right one.
3. **CI proves both.** The test suite runs on Windows and on macOS for every pull request. A test
   that only makes sense on one platform says so (see Tests below) instead of failing on the other.
4. **Shared code stays shared.** Only the thin layer that talks to the OS is per platform.
5. **Never give up a guarantee on one platform to get parity on the other.** For example, the
   Windows keyboard hook only ever acts on Win+Shift+S and Print Screen; the Mac shortcut is
   `RegisterEventHotKey` (sees only its own combination), never an event tap (which needs Input
   Monitoring and sees every key).

## How the code is laid out

- **One module per feature with a small, platform-neutral API**, and the OS calls behind it in
  `_<name>_win32.py` and `_<name>_mac.py` (`pointer.py` and `_pointer_win32.py`,
  `scroll_input.py` and `_scroll_win32.py`, `shortcuts.py`). Callers never check the platform; they
  ask the module (`scroll_input.available()`, `GlobalShortcuts.supported`).
- **No OS-only import at the top of a shared module.** `ctypes.WinDLL`, `win32api`, `winrt`,
  `comtypes`, PyObjC and so on are imported inside the platform module, which itself is imported
  only on its platform. Otherwise the other platform can't even start.
- **Platform-only dependencies are marked** in `requirements.txt`
  (`; sys_platform == "win32"`, `; sys_platform == "darwin"`).
- **Opening files and folders goes through Qt** (`QDesktopServices.openUrl`), never
  `os.startfile` or `explorer.exe`.

## Tests

- **Write tests that hold on both platforms.** A test that depends on a Windows-only feature checks
  for it instead of assuming it. The capture toolbar has no Scrolling button where scrolling isn't
  available, for example, so a test that walks the toolbar's buttons uses the ones that exist.
- **Truly Windows-only tests** (registry, Explorer, Win32 calls) carry `@pytest.mark.windows`.
  macOS-only tests carry `@pytest.mark.mac`. Each runs only on its platform.
- **Pretend to be a Mac in a unit test** with the `on_a_mac` fixture (`tests/conftest.py`). It makes
  the modules you pass see `sys.platform == "darwin"`, so the Mac paths are covered on a Windows
  machine too.
- **What CI can't prove on a Mac** (permissions, clicking menus, dragging between apps) goes in
  MAC-PLAN.md's test plan, for a person with a Mac to check.

## macOS gotchas

Things that look the same as on Windows and aren't. Add to this list whenever one bites.

- **Ctrl and Meta swap.** On a Mac, Qt's `ControlModifier` is the ⌘ Command key and `MetaModifier`
  is the Control key. Shortcuts written `Ctrl+...` become ⌘ automatically. Code that reads
  `MetaModifier` as "the Windows key" (e.g. `shortcuts.py`) means something else on a Mac.
- **Screen Recording permission.** Without it, a screen grab on macOS shows little more than the
  desktop. Check `CGPreflightScreenCaptureAccess()` before snipping and explain how to turn it on;
  the switch only takes effect after SnipForge is quit and reopened. With the free ad-hoc signature,
  macOS asks again after every update.
- **Points versus pixels.** Most macOS APIs work in points (a Retina screen has 2 pixels per point),
  and Cocoa puts the origin at the bottom-left; Quartz window bounds use the top-left. Convert
  explicitly, and test with a device-pixel ratio of 2.
- **The menu bar icon isn't the tray.** Clicking it opens its menu; it doesn't fire "activated" the
  way a Windows tray click does. Anything a tray click does needs a menu item on a Mac.
- **Opening the app again doesn't start a second copy.** macOS just activates the running one, so
  SnipForge has to react to being activated (show the Library), not to a second launch.
- **Overlay windows.** Frameless, stay-on-top tool windows can hide when the app isn't active, and
  the menu bar and other Spaces need the right window level. Test the capture overlay on a Mac.
- **Fonts.** Segoe UI and Consolas don't exist on macOS. Use the system font (or a font list with a
  fallback).
- **Where files go.** Bundled files are in `SnipForge.app/Contents/Resources`, not next to the
  executable. Caches go in `~/Library/Caches`, and settings (QSettings) in a plist in
  `~/Library/Preferences`.
- **Wording.** It's the Trash (not the Recycle Bin), the menu bar (not the tray), Finder (not
  Explorer), and ⌘ (not Ctrl) in messages.
- **Windows-only technology has no Mac twin by default.** DXGI, WASAPI, Media Foundation, COM,
  Outlook automation, WinRT OCR and the registry all need a different Mac implementation
  (ScreenCaptureKit, AVFoundation, Vision, managed preferences...). Plan the Mac half when you plan
  the feature.
- **The bundle identifier never changes** (`io.github.nighthawk909.snipforge`). macOS keys
  permissions, login items and IT profiles to it, as the installer's `AppId` is on Windows.
- **Gatekeeper.** The free build isn't notarized, so the first launch needs System Settings >
  Privacy & Security > Open Anyway. Release notes and the download page say so.
- **Compiled dependencies must be universal2 or get fused.** SnipForge.app is one build for Apple
  silicon and Intel, so every compiled file in it needs both. A dependency PyPI ships one
  architecture at a time (Pillow) goes in `SPLIT_WHEELS` in `scripts/universal.py`, which fuses
  both wheels with `lipo` before PyInstaller runs and checks the built app.

## Checklist for a pull request

- [ ] Works on Windows, and on macOS (or steps aside cleanly there, with a PARITY.md row).
- [ ] No OS-only import at the top of a shared module.
- [ ] Tests pass on both platforms in CI; platform-only tests are marked.
- [ ] Wording and shortcuts read right on a Mac (⌘, Trash, menu bar, Finder).
- [ ] PARITY.md updated if a feature's status changed on either platform.
