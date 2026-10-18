---
name: cross-platform
description: Keep SnipForge's Windows and Mac versions at parity. Use before adding any feature, and before changing anything that talks to the operating system (capture, shortcuts, window lists, OCR, sharing, start at sign-in, updates, recording, packaging, CI or releases), or when a test or build fails on one platform only.
---

# Cross-platform SnipForge

SnipForge ships for Windows and macOS from one codebase, and the Mac version stays at parity with
Windows. The full rules are in `docs/CROSS-PLATFORM.md`; feature status per platform is in
`docs/PARITY.md`; macOS APIs and permissions are in `docs/MAC-PLAN.md`. Read CROSS-PLATFORM.md
before you start.

## Before you write code

1. Find the feature's row in `docs/PARITY.md`. Plan **both** halves: the Windows one and the Mac one
   (MAC-PLAN.md names the macOS API for each OS integration).
2. Keep the shared module platform-neutral. Put OS calls in `_<name>_win32.py` and
   `_<name>_mac.py`, and import them only on their platform. Callers ask the module what's
   available (`available()`, `.supported`); they never check `sys.platform` themselves.

## While you write it

- No `ctypes.WinDLL`, `win32api`, `winrt`, `comtypes` or PyObjC import at the top of a shared module.
- On a Mac, Qt's `ControlModifier` is ⌘ and `MetaModifier` is the Control key.
- Mac pixels: points × device-pixel ratio (2 on Retina); Cocoa's origin is bottom-left.
- Screen capture on a Mac needs the Screen Recording permission: check it and explain it.
- Open files and folders with `QDesktopServices.openUrl`, not `os.startfile` or `explorer`.
- Mac wording: Trash, menu bar, Finder, ⌘.
- A feature that can't work on one platform yet must step aside there (hidden or explained), never
  fail, and its PARITY.md row says so.

## Tests

- Tests must pass on Windows and macOS. Don't assume a Windows-only feature exists (e.g. the
  Scrolling button); check for it.
- Mark platform-only tests `@pytest.mark.windows` or `@pytest.mark.mac`.
- Cover Mac code paths on any machine with the `on_a_mac` fixture (`tests/conftest.py`).
- Run `python -m pytest -q`; CI runs the suite on macOS too.

## Before the PR

- Fill in the "Platforms" checklist in the PR template.
- Update `docs/PARITY.md` if a feature's status changed on either platform.
- If you hit a new macOS (or Windows) surprise, add it to the gotchas in `docs/CROSS-PLATFORM.md`.
