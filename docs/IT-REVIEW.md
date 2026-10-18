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
| Settings: tool styles, Beautify on/off, theme (light/dark/match Windows) and accent, recent Teams recipients' email addresses, update choices (mode, channel, pinned or skipped version, the waiting download), capture choices (mode, delay, pointer, Fixed size), shortcuts of your own, which version of the private-window check last swept the Library, and the last snip's area for Repeat last snip (the monitor's name, position, size and scale, and a rectangle on it; no image) | `HKCU\Software\SnipForge\SnipForge` | Yes |
| Small theme images (check mark, arrows) regenerated at start | `%LOCALAPPDATA%\SnipForge\cache` | Yes |
| Per snip in the library: `meta.json` with the app name and window title it came from (best effort: windows recognised as private browsing, such as InPrivate, Incognito, Private Browsing and their equivalents in other browsers and several languages, keep only the app name; titles an older version saved are always hidden, and cleared from these files in the background; a file locked at the time is cleared at a later start), favorite, tags, and the words Windows OCR read in the finished snip (for Library search; read offline, cleared and re-read after each edit, so blurred or cropped text doesn't stay searchable) | `%USERPROFILE%\Pictures\SnipForge\Library\<snip>` | No (it's the user's work) |
| Screen recordings: `recording.mp4` in a folder of its own per recording (H.264 video, AAC sound), with `recording.json` (its length and size, written once it's finished, which is when the Library lists it), `preview.png` (a frame from it for its Library card, made the first time it's shown) and, once it's tagged or made a favorite, `meta.json` | `%USERPROFILE%\Pictures\SnipForge\Library\<recording>` | No (it's the user's work) |
| Recording choices: microphone and computer sound on or off, the microphone's device ID, pointer effects, countdown, the camera on or off and its device ID, and its bubble's shape, size and place on screen | `HKCU\Software\SnipForge\SnipForge\video` | Yes |
| Theme packs (optional, read-only JSON colors; see [THEMES.md](THEMES.md)) | `themes\` next to `SnipForge.exe`, and `%LOCALAPPDATA%\SnipForge\themes` | Install folder: yes. User folder: no |
| Temporary copies for drag-out and sharing (auto-deleted after 24 hours) and downloaded updates (`updates` subfolder, removed once installed) | `%TEMP%\SnipForge` | Yes |
| Startup entry (if enabled) | `HKCU` or, for all-users installs, `HKLM\...\CurrentVersion\Run` | Yes |
| OneDrive shares | `<OneDrive>\SnipForge` (synced by the user's OneDrive client) | No |
| Trimmed copies and GIFs made in the video editor, under a new name (`<video> (trimmed).mp4`, `<video>.gif`); the original recording is never changed. While one is being written it's a `.<name>.<random>.partial.mp4` (or `.gif`) file in the same folder, renamed when done and deleted if cancelled, failed or SnipForge quits first (only a crash or power cut can leave one behind) | Next to the original video | No (it's the user's work) |

Note that a snip's original, uncropped capture is kept so crops can be undone; deleting a snip from
the Library sends its whole folder to the Recycle Bin.

## Behaviors a security tool may flag, and why

**Low-level keyboard hook** (`snipforge/hotkey.py`). To replace Win+Shift+S, SnipForge installs a
`WH_KEYBOARD_LL` hook, the standard Windows mechanism for global hotkeys that `RegisterHotKey` can't
claim. The hook only acts on **S while Win+Shift are held** and **Print Screen with no modifiers**
(Alt+Print Screen and Win+Print Screen are left to Windows). Every other keystroke passes straight
through. Nothing is logged, stored or sent anywhere. Endpoint tools sometimes flag any keyboard hook
as keylogger-like; this is a false positive, and the ~150-line source shows the full logic.

**Shortcuts of your own** (optional, `snipforge/shortcuts.py`). In Settings > Capture a user can
add key combinations for New snip, Repeat last snip and Open the Library. These are registered with
`RegisterHotKey`, not the hook: Windows tells SnipForge only when one of its own combinations is
pressed and never shows it any other keystroke. Each needs Ctrl, Alt or the Windows key; a
combination another app already registered is refused. None are set by default.

**Screen capture.** The screen is read only when the user asks for a snip: the hotkey, New snip, Repeat last snip, or one of their own shortcuts. Repeat last snip reads the same area again straight away, without the chooser. A second `SnipForge.exe --snip` or `--repeat` started by the same user is passed to the running copy over a local per-user pipe, which is how the Start menu and taskbar entries reach it.

**Screen recording.** The screen is recorded only after the user chooses Record video (the tray,
the Record switch on the capture toolbar, or a shortcut they set) and then an area, and only that
area. Before recording starts, the panel beside the area opens the microphone and the computer's
sound only to show their levels; they're let go when Record or Cancel is clicked. Unless the user
picked a particular microphone, the recording uses Windows' default devices and follows them: when
Windows makes another device the default (a headset plugged in), the recording moves to it within
about a second. While recording,
the recorder process has them open (Windows shows its microphone-in-use icon), and SnipForge's
control bar (pause, stop, discard) stays on screen, kept out of the recording itself. Nothing is sent anywhere:
the recording is saved in the Library folder (see Data on the PC). While it records (not while it's
paused), the recorder process asks Windows not to sleep or turn the screen off for inactivity
(`SetThreadExecutionState`, the request video players and calls make), so, like a video call, it
can keep the screen saver and the screen's inactivity timeout from starting until the recording
ends or is paused; the request ends with the recording (or the process). SnipForge doesn't change
any power or lock setting, and locking (Win+L, or a policy lock) still happens. Sleep the user asks
for (closing the lid, Start > Sleep) still happens: the recorder first tries to finish the file
(it has about 1.5 s; if that isn't enough, it finishes on waking). Locking Windows pauses the
recording: the locked screen can't be recorded, and while paused nothing from the microphone is
written to the file (the devices stay open, so Windows' microphone-in-use icon stays on). It's
resumed from the control bar.

**Webcam** (optional, off until the user switches Camera on when getting ready to record). The
camera is opened only while that switch is on, for the bubble that shows it and for the recording;
Windows shows its camera light. Before opening it, SnipForge reads Windows' camera privacy setting
(`...\CapabilityAccessManager\ConsentStore\webcam` under `HKLM` and `HKCU`, read only) so it can say
"blocked by your privacy settings" rather than fail vaguely. SnipForge passes each picture to its
recorder process through a block of shared memory named `sfcam-<random>` (in the user's session,
freed when both processes let go); nothing is saved apart from the recording itself. The Camera
switch isn't remembered as on: it's off each time until the user switches it on.

**Startup entry** (optional). Written to the `Run` key so the hotkey works after sign-in.

**FFmpeg libraries** (`avcodec-63.dll`, `avformat-63.dll` and friends in `_internal\PySide6`). They
come with Qt Multimedia, the part of Qt that encodes video for the coming screen recording feature
(see `THIRD_PARTY_NOTICES.md`). They contain code for network protocols, but SnipForge only ever
gives them local file paths, with FFmpeg's protocol whitelist set to `file`, so they can't go
online. The video editor (trim, GIF export, playback) uses them the same way, and limits Qt's own
player to local files too (`QT_FFMPEG_PROTOCOL_WHITELIST=file`).

**Pillow** (`_internal\PIL`). The Python imaging library, used only to write GIFs from the video
editor. Only its core is bundled (no font, colour management, WebP or AVIF modules); it reads and
writes local files only.

**A second SnipForge process while recording** (`SnipForge.exe --record <channel>`). Each screen
recording is made by its own copy of `SnipForge.exe`, started by SnipForge and ending when the
recording does, so nothing else SnipForge does can stall or lose a recording. The two talk over a
local named pipe, `\\.\pipe\SnipForge-recorder-<random>` (no user name in it: any local user can list
pipe names), that only the signed-in user can open, and only about the recording (its settings,
pause/stop, how it went). It listens for Windows' sleep, shutdown and sign-out notices to finish the
video file first (that takes well under a second); if Windows ever has to wait on it, the reason it
can show is "Saving your screen recording". It also asks to be told when the screen turns off or on
(`RegisterPowerSettingNotification`, `GUID_CONSOLE_DISPLAY_STATE`) and when Windows is locked or
unlocked (`WTSRegisterSessionNotification`, this session only), to pause or warn as above. The packaged self-test starts one and has it answer.

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
`github.com` or GitHub's file storage hosts (the server and hosts named in `snipforge/project.py`),
and a feed's installers must sit in the feed's own folder (or under its https address). Every installer is checked against its SHA-256 before it's saved, and
again right before it runs. Release notes are shown as plain text, never as HTML.

GitHub allows 60 anonymous requests an hour per public IP address. If many PCs share one address
(an office behind NAT), some checks may be refused and retried later; a feed avoids this.

SnipForge never signs in to GitHub. A copy of SnipForge built from a private or internal repository
(see [PORTING.md](PORTING.md)) can't read its own releases; **Check for updates** then explains that
and asks people to have IT set the `UpdateFeed` policy. Automatic checks fail quietly; until the
policy is set, each PC sends one refused anonymous request every six hours and each time SnipForge
starts.

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
  permissions except the release-publishing job, and the jobs that code-sign a commit already on
  `main` (releases and previews of it), which can request an OpenID Connect token to sign in to
  Azure. CI and builds of unmerged branches can't.
- **Build provenance** and **code scanning (CodeQL)** turn on automatically once the repository is
  public. With them, release files can be verified with
  `gh attestation verify SnipForge-<version>-setup.exe --repo nighthawk909/snipforge`.
- **Code signing**: releases aren't Authenticode-signed yet, so SmartScreen may warn on first run.
  The release pipeline signs `SnipForge.exe`, the installer and its uninstaller with Azure Artifact
  Signing once the maintainer's signing account is set up ([CODE-SIGNING.md](CODE-SIGNING.md));
  signatures are timestamped and verified before release. Until then: allow-list by SHA-256 hash,
  sign with your own certificate, or build from source internally (`python scripts/build.py --installer`).
  Deploying through Software Center avoids the SmartScreen prompt either way.

## Verifying a download

```powershell
Get-FileHash .\SnipForge-0.4.0-setup.exe -Algorithm SHA256
# compare with the matching line in SHA256SUMS.txt from the same release
```
