# SnipForge: working agreement for AI coding agents

SnipForge is a Python/PySide6 screenshot and markup tool for Windows, released through GitHub
Releases as a per-user installer. These rules make agent-driven changes safe to ship.

## Every change

1. Work on a branch (`feat/...`, `fix/...`), never directly on `main`.
2. Write or update tests first for behavior changes; UI gestures are tested with real mouse events
   (`tests/test_editor.py` helpers `send`, `gesture`, `drag`).
3. Run before every commit:
   - `python -m ruff check .`
   - `python -m pytest -q`
   - For anything touching Windows integrations (share, Outlook, Teams, OneDrive, hotkey, clipboard):
     `QT_QPA_PLATFORM=windows python -m pytest -m desktop`. Unit tests replace these with fakes,
     so only the desktop suite proves the real feature works. Add a desktop test with every new
     integration.
   - For packaging, dependency or startup changes: `python scripts/build.py` and then
     `dist/SnipForge/SnipForge.exe --self-test report.json` (must exit 0).
4. Commit with conventional commits. The subject line becomes the user-facing release note, so
   write it for users: `fix(crop): Esc cancels a crop in progress`, not `fix: handle key event`.
5. Open a PR with `gh pr create`; include what changed, why, and how it was verified. CI must be
   green before merge.
6. After a feature lands, have the change reviewed (code-reviewer agent) and fix high/medium
   findings in the same PR.

## Releases

- Never edit version numbers or `CHANGELOG.md` by hand; release-please does both in its release PR.
- Merging the release PR publishes the release and the build attaches the installer. Check the
  Release workflow run and confirm the assets (setup.exe, portable zip, SHA256SUMS.txt,
  sbom.cdx.json) are on the release.
- Ask the maintainer before merging a release PR or changing repo visibility, branch protection or
  secrets.

## Guardrails

- No telemetry, analytics or background network calls. Network use must be user-initiated and
  documented in `docs/IT-REVIEW.md`.
- No secrets in the repo; CI uses only the built-in `GITHUB_TOKEN`.
- Pin GitHub Actions to full commit SHAs; pin Python dependencies in `requirements.txt`.
- The keyboard hook (`snipforge/hotkey.py`) must only ever act on Win+Shift+S and Print Screen.
- Keep the installer per-user (no admin) and the `AppId` in `packaging/installer.iss` unchanged.
