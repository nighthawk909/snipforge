# SnipForge: security and IT review guide

A summary for IT and security teams evaluating SnipForge for company laptops. Everything here can
be checked in the source code; file references are given where useful.

## Summary

| | |
|---|---|
| What it is | Screenshot capture and markup tool (SnagIt-style) for Windows 10/11 |
| License | MIT (open source); bundled components and their licenses in `THIRD_PARTY_NOTICES.md` |
| Install | Per-user to `%LOCALAPPDATA%\Programs\SnipForge`; **no admin rights needed** |
| Machine-wide | Optional, needs admin: `SnipForge-<version>-setup.exe /VERYSILENT /ALLUSERS /TASKS="autostart"` |
| Network | A once-a-day update check against GitHub (can be turned off by the user or by policy), plus Share actions the user starts. Nothing else (details below) |
| Telemetry | None. No accounts, analytics or crash reporting |
| Updates | Asks before updating. Per-user installs download the new installer from the GitHub release, verify its SHA-256 against the release's `SHA256SUMS.txt`, and install silently. All-users installs only point to the download page |
| Elevation | The app never runs elevated; only an all-users install needs admin |

## Data on the PC

| What | Where | Removed on uninstall? |
|---|---|---|
| The user's snips (original capture, editable markup, rendered image) | `%USERPROFILE%\Pictures\SnipForge` | No (it's the user's work) |
| Settings: tool styles, Beautify on/off, theme (light/dark/match Windows) and accent, recent Teams recipients' email addresses | `HKCU\Software\SnipForge\SnipForge` | Yes |
| Small theme images (check mark, arrows) regenerated at start | `%LOCALAPPDATA%\SnipForge\cache` | Yes |
| Theme packs (optional, read-only JSON colors; see [THEMES.md](THEMES.md)) | `themes\` next to `SnipForge.exe`, and `%LOCALAPPDATA%\SnipForge\themes` | Install folder: yes. User folder: no |
| Temporary copies for drag-out and sharing (auto-deleted after 24 hours) and downloaded updates | `%TEMP%\SnipForge` | Yes |
| Startup entry (if enabled) | `HKCU` or, for all-users installs, `HKLM\...\CurrentVersion\Run` | Yes |
| OneDrive shares | `<OneDrive>\SnipForge` (synced by the user's OneDrive client) | No |

Note that a snip's original, uncropped capture is kept so crops can be undone; deleting a snip from
the Library sends its whole folder to the Recycle Bin.

## Behaviors a security tool may flag, and why

**Low-level keyboard hook** (`snipforge/hotkey.py`). To replace Win+Shift+S, SnipForge installs a
`WH_KEYBOARD_LL` hook, the standard Windows mechanism for global hotkeys that `RegisterHotKey` can't
claim. The hook only acts on **S while Win+Shift are held** and **Print Screen with no modifiers**
(Alt+Print Screen and Win+Print Screen are left to Windows). Every other keystroke passes straight
through. Nothing is logged, stored or sent anywhere. Endpoint tools sometimes flag any keyboard hook
as keylogger-like; this is a false positive, and the ~150-line source shows the full logic.

**Screen capture.** The screen is read only after the hotkey is pressed or the user chooses New snip.

**Startup entry** (optional). Written to the `Run` key so the hotkey works after sign-in.

**Explorer right-click commands** (`snipforge/share.py`). The Share menu invokes the same
right-click commands a user would click by hand (OneDrive "Copy link"/"Share", Windows "Share") on
the exported image.

## Network access

| Action | Destination | What's sent |
|---|---|---|
| Update check (daily, and on Check for updates) | `api.github.com/repos/nighthawk909/snipforge/releases/latest` | A GET request; no user data |
| Update download (only after the user clicks Update now) | `github.com/nighthawk909/snipforge/releases/download/...` (redirects to GitHub's file storage) | Downloads the installer and checksum file |
| Share → OneDrive | The user's own signed-in OneDrive client | The PNG is saved into the OneDrive folder; OneDrive syncs it |
| Share → Outlook | Classic Outlook via COM on the same PC | Opens a draft; nothing is sent until the user clicks Send |
| Share → Teams | The Teams desktop app (`msteams:` link with the chosen recipients) | Opens the chat; the user pastes and sends |

## Turning update checks off

Users can untick **Update automatically** in the tray menu. Organizations can turn checks off for
everyone (this also hides the option) with a DWORD policy value:

```
HKLM\Software\Policies\SnipForge  DisableUpdateCheck = 1
```

## Supply chain

- **Builds** run on GitHub-hosted runners from the tagged source (`.github/workflows/build.yml`).
  Installer files are attached to a release only after the packaged app passes its self-test
  (`SnipForge.exe --self-test`: editor, icons, OCR, keyboard hook).
- **Checksums**: every release has `SHA256SUMS.txt`.
- **SBOM**: every release has `sbom.cdx.json` (CycloneDX), generated from a clean environment
  containing only the runtime dependencies in `requirements.txt`.
- **Dependencies** are pinned (`requirements.txt`) and updated through Dependabot pull requests that
  must pass CI. GitHub Actions are pinned to full commit SHAs. Workflows run with read-only
  permissions except the release-publishing job.
- **Build provenance** and **code scanning (CodeQL)** turn on automatically once the repository is
  public. With them, release files can be verified with
  `gh attestation verify SnipForge-<version>-setup.exe --repo nighthawk909/snipforge`.
- **Code signing**: releases aren't Authenticode-signed yet, so SmartScreen may warn on first run.
  Options: allow-list by SHA-256 hash, or build from source internally (`python scripts/build.py --installer`).

## Verifying a download

```powershell
Get-FileHash .\SnipForge-0.4.0-setup.exe -Algorithm SHA256
# compare with the matching line in SHA256SUMS.txt from the same release
```
