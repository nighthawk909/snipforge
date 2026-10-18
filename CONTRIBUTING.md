# Contributing to SnipForge

## Setup

```bash
pip install -r requirements-dev.txt
python -m snipforge      # run from source
python -m pytest         # unit tests (headless, fast)
QT_QPA_PLATFORM=windows python -m pytest -m desktop   # real Windows features (opens windows)
python -m ruff check .   # lint
```

## How a change gets to users

1. **Branch** from `main`: `feat/<short-name>`, `fix/<short-name>`, `docs/...`.
2. **Test first** for behavior changes: add or update tests under `tests/`. UI gestures are tested
   with real mouse events (see `tests/test_editor.py`).
3. **Commit** with [conventional commits](https://www.conventionalcommits.org/). These messages
   *are* the release notes:
   - `feat(crop): drag edges back out to restore cut areas` → listed under Features, minor bump
   - `fix(share): Teams drop no longer reports a missing file` → listed under Bug fixes, patch bump
   - `docs:`, `test:`, `refactor:`, `ci:`, `chore:` → no release on their own
   - `feat!:` or a `BREAKING CHANGE:` footer → major bump
4. **Pull request** to `main`, titled like a conventional commit (checked automatically; PRs are
   squash-merged so the title becomes the release note). CI must pass: lint, tests, and a full
   installer build whose packaged app runs a self-test.
5. **Merge** (squash). release-please opens or updates a **release PR** with the version bump and
   CHANGELOG entry, and CI is started on it automatically.
6. **Release** by merging the release PR. That tags `vX.Y.Z`, creates a draft GitHub release, builds
   the installer, attaches the installer, portable zip, checksums and SBOM, and then publishes the
   release, so download links never point at a release that doesn't have its installer yet.

## Building locally

```bash
python scripts/build.py              # dist/SnipForge/SnipForge.exe + portable zip
python scripts/build.py --installer  # + dist/SnipForge-<version>-setup.exe (needs Inno Setup 6)
dist/SnipForge/SnipForge.exe --self-test report.json   # proves the packaged app works
```

## Design notes

- `snipforge/editor/canvas.py`: tools, edits, crop, undo and export. Its mixins hold the rest:
  `canvas_input.py` (keyboard and mouse), `canvas_draw.py` (what gestures create) and
  `canvas_view.py` (zoom, fit, background). Annotations live in `items.py`,
  `text_items.py` and `stamps.py`; each serializes with `get_state()`/`set_state()`, which undo,
  duplicate and the library all build on.
- `snipforge/capture.py`: the freeze-frame overlay. `hotkey.py`: the Win+Shift+S hook.
- `snipforge/share.py` and `editor/share_menu.py`: Outlook, Teams, OneDrive and Windows share.
