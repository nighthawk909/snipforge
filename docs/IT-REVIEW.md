# SnipForge: security and IT review guide

A summary for IT and security teams evaluating SnipForge for company laptops. Everything here can
be checked in the source code; file references are given where useful.

## Summary

| | |
|---|---|
| What it is | Screenshot capture and markup tool (SnagIt-style) for Windows 10/11 |
| License | MIT (open source); bundled components in `THIRD_PARTY_NOTICES.md` |
| Install | Per-user to `%LOCALAPPDATA%\Programs\SnipForge`, **no admin rights**. Machine-wide: `setup.exe /VERYSILENT /ALLUSERS` |
| Network | **None by default.** Only when the user clicks Share or Check for updates (details below) |
| Telemetry | None. No accounts, analytics, crash reporting or auto-update |
| Data stored | Screenshots the user takes, in `%USERPROFILE%\Pictures\SnipForge`; settings in `HKCU\Software\SnipForge` |
| Elevation | Never requests admin rights |
| Uninstall | Settings → Apps → SnipForge. Removes the program and startup entry; leaves the user's own snips |

## Behaviors a security tool may flag, and why

**Low-level keyboard hook** (`snipforge/hotkey.py`). To replace Win+Shift+S, SnipForge installs a
`WH_KEYBOARD_LL` hook, the standard Windows mechanism for global hotkeys that `RegisterHotKey` can't
claim. The hook only checks whether the key is **S with Win+Shift held** or **Print Screen**. Every
other keystroke is passed straight through. Nothing is logged, stored or sent anywhere. Endpoint
tools sometimes flag any keyboard hook as keylogger-like; this is a false positive, and the
~100-line source shows the full logic.

**Screen capture.** The screen is read only after the hotkey is pressed or the user chooses New snip.

**Startup entry** (optional). If the user enables it, a value is written to
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` so the hotkey works after sign-in.

**Explorer right-click commands** (`snipforge/share.py`). The Share menu invokes the same
right-click commands a user would click by hand (OneDrive "Copy link", Windows "Share") on the
exported image.

## Network access (only on user action)

| Action | Destination | What's sent |
|---|---|---|
| Check for updates | `api.github.com/repos/nighthawk909/snipforge/releases/latest` | A GET request; no user data |
| Share → OneDrive | The user's own signed-in OneDrive client | The PNG is saved into the OneDrive folder; OneDrive syncs it |
| Share → Outlook | Classic Outlook via COM on the same PC | Opens a draft; nothing is sent until the user clicks Send |
| Share → Teams | The Teams desktop app (`msteams:` link) | Opens a chat; the user pastes and sends |

## Supply chain

- **Builds** run on GitHub-hosted runners from the tagged source (`.github/workflows/build.yml`).
  The packaged app runs a self-test before anything is published.
- **Checksums**: every release has `SHA256SUMS.txt`.
- **SBOM**: every release has `sbom.cdx.json` (CycloneDX) listing every bundled package and version.
- **Build provenance**: release files carry a GitHub artifact attestation, verifiable with
  `gh attestation verify SnipForge-<version>-setup.exe --repo nighthawk909/snipforge`.
- **Dependencies** are pinned (`requirements.txt`) and updated through Dependabot pull requests that
  must pass CI. GitHub Actions are pinned to full commit SHAs.
- **Code scanning**: CodeQL runs on every change and weekly.
- **Code signing**: releases aren't Authenticode-signed yet, so SmartScreen may warn on first run.
  Options: allow-list by SHA-256 hash, or build from source internally (`python scripts/build.py --installer`).

## Verifying a download

```powershell
Get-FileHash .\SnipForge-0.4.0-setup.exe -Algorithm SHA256
# compare with the matching line in SHA256SUMS.txt from the same release
```
