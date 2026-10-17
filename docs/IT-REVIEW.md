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
| Network | A once-a-day update check against GitHub or a feed you host (can be turned off by the user or by policy), a background download of a new version in the Automatic and Ask modes, plus Share actions the user starts. Nothing else (details below) |
| Telemetry | None. No accounts, analytics or crash reporting |
| Updates | Three modes: Automatic, Ask before installing (default) and Manual. Per-user installs download the new installer, verify its SHA-256 (against the release's `SHA256SUMS.txt`, or the feed's `releases.json`) and install silently. All-users installs only point to the download page. Policy can force the mode, channel and source (details below) |
| Elevation | The app never runs elevated; only an all-users install needs admin |

## Data on the PC

| What | Where | Removed on uninstall? |
|---|---|---|
| The user's snips (original capture, editable markup, rendered image) | `%USERPROFILE%\Pictures\SnipForge` | No (it's the user's work) |
| Settings: tool styles, Beautify on/off, theme (light/dark/match Windows) and accent, recent Teams recipients' email addresses, update choices (mode, channel, pinned or skipped version, the waiting download) | `HKCU\Software\SnipForge\SnipForge` | Yes |
| Small theme images (check mark, arrows) regenerated at start | `%LOCALAPPDATA%\SnipForge\cache` | Yes |
| Theme packs (optional, read-only JSON colors; see [THEMES.md](THEMES.md)) | `themes\` next to `SnipForge.exe`, and `%LOCALAPPDATA%\SnipForge\themes` | Install folder: yes. User folder: no |
| Temporary copies for drag-out and sharing (auto-deleted after 24 hours) and downloaded updates (`updates` subfolder, removed once installed) | `%TEMP%\SnipForge` | Yes |
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
| Update check (daily unless updates are Manual, and on Check for updates) | `api.github.com/repos/nighthawk909/snipforge/releases?per_page=20`, or the feed set by policy | A GET request; no user data |
| Update download: in the background in Automatic and Ask modes, otherwise only after the user chooses to install | `github.com/nighthawk909/snipforge/releases/download/...` (redirects to GitHub's file storage), or the feed's folder | Downloads the installer and checksum file |
| Share → OneDrive | The user's own signed-in OneDrive client | The PNG is saved into the OneDrive folder; OneDrive syncs it |
| Share → Outlook | Classic Outlook via COM on the same PC | Opens a draft; nothing is sent until the user clicks Send |
| Share → Teams | The Teams desktop app (`msteams:` link with the chosen recipients) | Opens the chat; the user pastes and sends |

Update traffic is https only, and redirects can't downgrade to http. GitHub downloads must come from
`github.com` or GitHub's file storage hosts, and a feed's installers must sit in the feed's own folder
(or under its https address). Every installer is checked against its SHA-256 before it's saved, and
again right before it runs. Release notes are shown as plain text, never as HTML.

GitHub allows 60 anonymous requests an hour per public IP address. If many PCs share one address
(an office behind NAT), some checks may be refused and retried later; a feed avoids this.

## Controlling updates

**Modes** (the user's choice unless policy sets one):

| Mode | Checks | Downloads | Installs |
|---|---|---|---|
| Automatic | Daily | In the background | The next time SnipForge starts (or as it quits), then shows what changed |
| Ask before installing (default) | Daily | In the background | When the user clicks Install now (or Skip this version / Later) |
| Manual | Only when the user clicks Check for updates | When the user chooses | When the user chooses |

Users can also choose the **Preview** channel (prereleases as well as releases), **pin** a version so nothing
newer is offered, or **install an older version** (a rollback, which pins that version so it isn't replaced
straight away). Unticking **Update automatically** in the tray menu sets Manual.

**Policy.** Set these under `HKLM\Software\Policies\SnipForge` (or `HKCU\Software\Policies\SnipForge` for one
user). Machine values win over user values; for the two on/off values the more restrictive one wins.
Settings that policy forces are shown as locked ("Managed by your organization"). Only policy can change
where updates come from: the app's own settings can't point it at another address.

| Value | Type | Effect |
|---|---|---|
| `DisableUpdateCheck` | DWORD `1` | No update checks or downloads at all; the tray option is hidden |
| `UpdateSource` | String: `github` or `feed` | Where releases come from (setting `UpdateFeed` alone also means `feed`) |
| `UpdateFeed` | String (REG_SZ or REG_EXPAND_SZ) | A folder such as `\\fileserver\apps\SnipForge` or `%USERPROFILE%\Contoso\SnipForge Releases` (a SharePoint library synced by OneDrive), or an `https://` address. It holds `releases.json` and the installers it lists |
| `UpdateMode` | String: `auto`, `ask` or `manual` | Forces the mode |
| `UpdateChannel` | String: `stable` or `preview` | Forces the channel |
| `AllowRollback` | DWORD `0` | Blocks installing older versions |

For example, to have everyone update from a file share without being asked:

```
HKLM\Software\Policies\SnipForge  UpdateFeed = \\fileserver\apps\SnipForge   (REG_SZ)
HKLM\Software\Policies\SnipForge  UpdateMode = auto                          (REG_SZ)
```

If `UpdateSource` is `feed` but `UpdateFeed` is missing, or isn't an https address or a full folder path,
SnipForge reports the problem and doesn't fall back to GitHub. Policy is read when SnipForge starts.

**Feed format.** `releases.json` sits in the feed folder, next to the installers it lists:

```json
{"schema": 1, "releases": [
  {"version": "0.6.0", "channel": "stable", "published": "2026-10-20T15:00:00Z", "notes": "What changed",
   "assets": [{"platform": "windows", "name": "SnipForge-0.6.0-setup.exe", "sha256": "<64 hex characters>"}]}
]}
```

Copy each `sha256` from the release's `SHA256SUMS.txt`. Entries that don't follow this format are skipped.

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
