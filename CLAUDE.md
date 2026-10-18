# SnipForge: working agreement for AI coding agents

SnipForge is a Python/PySide6 screenshot, markup and recording tool for Windows and macOS, released
through GitHub Releases (a per-user installer for Windows, a .dmg for Mac). These rules make
agent-driven changes safe to ship.

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
   findings in the same PR. Say in the PR which `docs/ROADMAP.md` box it ticks.
7. **Both platforms.** Windows first, then the Mac port (`docs/ROADMAP.md`); the Mac build must
   always build and start, and features not on the Mac yet step aside there. Before touching anything
   that talks to the OS, or adding a feature, read `docs/CROSS-PLATFORM.md` (the rules, the code
   layout and the macOS gotchas; the `cross-platform` skill in `.claude/skills` sums it up). A
   feature ships on both platforms in the same release, or steps aside cleanly on the one that's
   behind and gets a row in `docs/PARITY.md`. Tests must pass on Windows and macOS.

## Releases

- Never edit version numbers or `CHANGELOG.md` by hand; release-please does both in its release PR.
- Merging the release PR creates the tag and a draft release; the Release workflow builds, attaches
  the installer and only then publishes it. Check the Release workflow run and confirm the release
  is published with its assets (setup.exe, portable zip, SHA256SUMS.txt, sbom.cdx.json). If the
  run fails the release stays a draft and the previous one stays "Latest": use "Re-run failed jobs"
  on that run (reuses a finished build), or run the Release workflow by hand with the tag.
- Keep `draft` and `force-tag-creation` together in `release-please-config.json`. GitHub only
  creates a draft's tag when it's published; without it the build can't check out the tag, and
  release-please can't find the previous release, so its next release PR lists the whole history.
- Ask the maintainer before merging a release PR or changing repo visibility, branch protection or
  secrets.

## Guardrails

- No telemetry, analytics or background network calls. Network use must be user-initiated and
  documented in `docs/IT-REVIEW.md`.
- No secrets in the repo; CI uses only the built-in `GITHUB_TOKEN`. The one exception is code
  signing, which signs in to Azure with GitHub's OIDC token (still no stored secret), only in jobs
  that build a commit already on `main` (`docs/CODE-SIGNING.md`). Never grant `id-token: write` to
  a job that builds unreviewed code, and never add a client secret or certificate file for signing.
- Pin GitHub Actions to full commit SHAs; pin Python dependencies in `requirements.txt`.
- The keyboard hook (`snipforge/hotkey.py`) must only ever act on Win+Shift+S and Print Screen.
  Any other global shortcut goes through `RegisterHotKey` (`snipforge/shortcuts.py`), never the hook.
- Keep the installer per-user (no admin) and the `AppId` in `packaging/installer.iss` unchanged.
