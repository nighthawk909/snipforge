# SnipForge for Mac: the plan

Coworkers on work Macs have asked for SnipForge. This is what it takes to give them a real Mac
version, using only free tools: no Apple Developer Program ($99 a year), so no Developer ID
signature and no notarization. Macs only (not iPhone or iPad).

**Where it stands:** SnipForge starts on a Mac and the editor and Library work. The
[macOS build](../.github/workflows/mac.yml) workflow (run by hand: Actions > macOS build > Run
workflow) runs the tests on macOS, builds `SnipForge.app` into `SnipForge-<version>.dmg`, installs
it from the .dmg, runs the packaged app's `--self-test`, starts the real app and launches it a second
time, then uploads the .dmg as the `snipforge-macos` artifact. Snipping works once macOS's Screen
Recording permission is given; the Windows integrations (shortcut, OCR, window picking, share, start
at sign-in, updates) each need a Mac version, listed below with the macOS API for each.

**What it costs:** nothing. macOS runners are free for public repositories, and every macOS API
below comes with macOS. The price of skipping the paid Apple account is the first-launch step in
[Gatekeeper: the first launch](#gatekeeper-the-first-launch) and the permission notes after it.

## What works today

Checked by the macOS build workflow (`--self-test` and a real start of the packaged app) and by
the unit tests running on macOS:

| Works on a Mac | Notes |
|---|---|
| Starting, the menu bar icon, one SnipForge at a time | A second start hands its command to the running SnipForge and exits, as on Windows (Qt's local socket works the same on macOS; CI checks it). Opened again from Finder or the Dock, macOS just activates the running SnipForge, which doesn't bring up its Library yet (phase 1). |
| Library: browse, open, favorites, tags, delete, Open library folder | Deleted snips go to the Trash. Open folder opens Finder. Text search finds titles, apps and tags but not words inside snips until OCR has its Mac version. |
| Editor: every markup tool, crop, Beautify, undo, copy, save, Drag out, Pin | Same code as Windows. Keyboard shortcuts written Ctrl+... become ⌘ on a Mac (Qt does this). |
| Settings, themes (light, dark, follow the system), accents | Start at sign-in reads as off and explains it isn't available yet. |
| Snipping an area or a full screen | Needs Screen Recording permission (below). Qt 6.12's screen grab on macOS uses ScreenCaptureKit, Apple's current capture API. |
| Share > Send in Teams, Save and show in folder | Teams opens through its `msteams:` link as on Windows; Show in folder uses Finder. Email falls back to copying the snip and opening the default mail app. |

The app is one universal build (Apple silicon and Intel) and needs **macOS 14 Sonoma or later**,
the oldest macOS that PySide6 6.12's wheels support.

## What needs a Mac version

Each row is Windows-only code today, already behind a platform check so the app runs without it.
The Mac version goes in a `_<name>_mac.py` module next to the existing `_<name>_win32.py` ones (the
capture session's `pointer.py` and `scroll_input.py` already work this way). Calls into macOS go
through [PyObjC](https://pyobjc.readthedocs.io/) (MIT licensed, free), added to `requirements.txt`
with `; sys_platform == "darwin"` the way pywin32 and WinRT are marked Windows-only.

| Feature | Windows today | macOS version | Permission it needs |
|---|---|---|---|
| Global shortcut | Low-level keyboard hook on Win+Shift+S and Print Screen (`hotkey.py`) | Carbon `RegisterEventHotKey`: registers exactly the chosen combination, sees no other keys. macOS keeps ⌘⇧3/4/5 for its own screenshots, so the default would be a free combination such as ⌘⇧2, with the option of taking ⌘⇧4 once the person turns off macOS's own in System Settings > Keyboard > Keyboard Shortcuts > Screenshots. | None. (A `CGEventTap` would need Input Monitoring; `RegisterEventHotKey` doesn't.) |
| Screen capture | Qt `grabWindow` | Already works: Qt 6.12 uses ScreenCaptureKit (`SCScreenshotManager`). Missing piece: check `CGPreflightScreenCaptureAccess()` before snipping and, if it's off, explain and offer to open the setting (`CGRequestScreenCaptureAccess()` adds SnipForge to the list). Without it a snip shows only the desktop and SnipForge's own windows. | Screen & System Audio Recording. macOS 15 and later can also ask the person, about monthly, to confirm an app may keep capturing the screen; that prompt is macOS's and can't be turned off by the app. |
| Window list (click a window to snip it, and the app name and title the Library shows) | `EnumWindows` + DWM frame bounds (`winrects.py`) | Quartz `CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly \| kCGWindowListExcludeDesktopElements, kCGNullWindowID)`: front to back, with `kCGWindowBounds` (points, so multiply by the screen's scale for pixels), `kCGWindowOwnerName`, `kCGWindowOwnerPID`, `kCGWindowLayer` (0 for normal windows) and `kCGWindowName`. | Bounds and app names need none; window titles need Screen Recording. |
| OCR (Grab Text, and Library search reading the words in snips) | Windows.Media.Ocr through WinRT (`ocr.py`) | Vision framework: `VNRecognizeTextRequest` (accurate level, automatic language) on a `VNImageRequestHandler`; each `VNRecognizedTextObservation` gives a line, and `boundingBoxForRange:` gives word boxes (normalized, origin bottom-left, so flip them). Offline and built in, like Windows OCR. | None. |
| Share | Explorer right-click commands (OneDrive copy link and share, the Windows share sheet) and Outlook through COM (`share.py`) | `NSSharingServicePicker` shown under the Share button (the button's `NSView` comes from `QWidget.winId()`): Mail, Messages, AirDrop, Notes and any app's share extension. Email: `NSSharingService` named `NSSharingServiceNameComposeEmail` with the snip attached opens the default mail app (Outlook when it's the default). OneDrive: sync folders are under `~/Library/CloudStorage/OneDrive-*`; saving there and showing it in Finder gives OneDrive's own Share and Copy Link commands. Teams already works. | None. |
| Start when I sign in | `HKCU\...\Run` registry value (`autostart.py`) | `SMAppService.mainApp` `register()`/`unregister()`/`status` (macOS 13+), which lists SnipForge in System Settings > General > Login Items & Extensions. If it turns out to need a Developer ID signature, fall back to a LaunchAgent plist in `~/Library/LaunchAgents` with `RunAtLoad`. | None; macOS tells the person it was added. IT can manage it (`com.apple.servicemanagement` payload, matched by bundle identifier). |
| Pointer in snips | Cursor drawn on black and on white (`_pointer_win32.py`, PR #28) | `NSCursor.currentSystemCursor` gives the image and hot spot; `NSEvent.mouseLocation` the position (points, origin bottom-left). | None. |
| Scrolling capture | `SendInput` wheel turns and `SetCursorPos` (`_scroll_win32.py`) | `CGEventCreateScrollWheelEvent` + `CGEventPost(kCGHIDEventTap)` to scroll, `CGWarpMouseCursorPosition` to place the pointer, `CGEventGetLocation(CGEventCreate(None))` to read it. | Accessibility (posting input to other apps). Check with `AXIsProcessTrustedWithOptions`. |
| Updates | Downloads the verified `SnipForge-<version>-setup.exe` and runs it silently (`update_platform.py`) | The update engine already knows `SnipForge-<version>.dmg` assets. Install: verify the download's SHA-256 (as today), `hdiutil attach -nobrowse -readonly`, `ditto` the new `SnipForge.app` beside the running one, check it with `codesign --verify`, swap the two, `hdiutil detach`, reopen with `open -n`. Files SnipForge downloads itself aren't quarantined, so no Gatekeeper step after an update. If the app's folder isn't writable, or macOS is running it from a read-only copy because it was never moved out of Downloads ("App Translocation": its path contains `/AppTranslocation/`), say so and open the .dmg instead. | None. |
| Policy (IT settings) | `HKLM`/`HKCU\Software\Policies\SnipForge` | Managed preferences: read the same names with `CFPreferencesCopyAppValue` for the bundle identifier; IT sets them with a configuration profile. `CFPreferencesAppValueIsForced` says which are locked. | None. |
| Smaller things | | Mac wording ("Trash", "menu bar", ⌘ instead of Ctrl in messages, the Library's "Press Win+Shift+S" hint, the About text says "for Windows"); bring up the Library when SnipForge is opened again from Finder or the Dock (macOS reactivates the running app instead of starting a second one); Settings > Open folder still uses `os.startfile` (`settings_window.py`); license files and organization theme packs are in `SnipForge.app/Contents/Resources`, but the app looks next to the executable; the theme cache goes to the temp folder instead of `~/Library/Caches`. | |

## Gatekeeper: the first launch

Without a paid Developer ID the app can't be notarized, so macOS won't open it from a download
until the person says so once. The build gives it a free ad-hoc signature (Apple silicon won't run
unsigned code at all) and checks it with `codesign --verify --deep --strict`; `spctl` reports
"rejected", which is expected.

On macOS 15 Sequoia and later the old Control-click > Open shortcut is gone. The steps, which the
.dmg also carries as **If macOS won't open SnipForge.txt**:

1. Open the .dmg and drag SnipForge to Applications (or to an Applications folder in your home
   folder if you can't write to the main one), then open it. macOS says it couldn't verify
   SnipForge is free of malware: click **Done**.
2. Open **System Settings > Privacy & Security**, scroll to **Security** and click **Open Anyway**
   next to the message about SnipForge. The button is there for about an hour after the attempt.
3. Confirm with **Open Anyway** again and your password (Touch ID works). From then on SnipForge
   opens like any other app.

Apple's guide: [Open a Mac app from an unknown developer](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unknown-developer-mh40616/mac).

Then, the first time they snip, macOS asks for **Screen & System Audio Recording**. They turn
SnipForge on in System Settings > Privacy & Security > Screen & System Audio Recording, then quit and
reopen it.

### What an ad-hoc signature means after that

- **Each update asks for Screen Recording again.** macOS ties the permission to the app's code
  signature. An ad-hoc signature is just a hash of that build, so every new version looks like a new
  app and the old switch in the list stops applying. Gatekeeper doesn't ask again (the updater's
  download isn't quarantined), but the permission does.
- **A free fix exists, but it's Josh's call.** Signing every build with one self-signed certificate
  (made free in Keychain Access) keeps the identity stable, so permissions survive updates.
  Gatekeeper still treats it as an unidentified developer. CI would need the certificate's private
  key as a repository secret, which `CLAUDE.md` doesn't allow today; it needs Josh's decision.

### What a managed work Mac (Jamf, Intune) may block

Check **System Settings > Privacy & Security > Profiles** (or "This Mac is supervised and managed
by..." under General > Device Management) to see whether a Mac is managed. On a managed Mac:

- **Open Anyway can be switched off.** A `com.apple.systempolicy.managed` profile with
  `DisableOverride` removes the button; IT may also allow only App Store and identified developers.
  Then only IT can let SnipForge run.
- **The password step may need an administrator.** Making the exception asks for credentials, and
  people with standard (non-admin) accounts can't always approve it themselves.
- **Screen Recording can't be pre-approved by IT.** Apple's privacy profiles (PPPC) can deny Screen
  Recording or let standard users turn it on, but never switch it on for them, so each person flips
  the switch once (and, with ad-hoc signing, after each update). Accessibility (needed only for
  scrolling capture) can be pre-approved, but PPPC matches the app's code signature, which an ad-hoc
  signature changes every release.
- **Allow-listing tools** (Jamf Protect, Santa, Microsoft Defender for Endpoint) can block unknown
  apps outright, whatever Gatekeeper says.
- **Writing to /Applications** needs an admin. Standard users can use `~/Applications` (create the
  folder in their home folder).

The smooth route for work Macs is IT deploying SnipForge themselves: apps installed through Jamf or
Intune aren't quarantined, so there's no Gatekeeper step at all, and IT can re-sign the app with the
company's own Developer ID if they have one, which also makes PPPC and managed login items work by
team identifier. That costs us nothing; [IT-REVIEW.md](IT-REVIEW.md) is what they'd read first.
Removing the quarantine flag by hand (`xattr -dr com.apple.quarantine SnipForge.app`) also skips
Gatekeeper, but on a work Mac that goes around IT's controls: only with IT's OK.

## Test plan for a coworker with a Mac

Getting the build: open the latest successful [macOS build](../.github/workflows/mac.yml) run in the
repository's Actions tab, download the `snipforge-macos` artifact (GitHub asks you to sign in for
artifacts) and unzip it. It holds `SnipForge-<version>.dmg`, `SHA256SUMS.txt` and the CI's
`selftest.json`. Until the .dmg is attached to preview releases (phase 1), a tester without a
GitHub account gets the .dmg from someone who has one.

Write down as you go: the Mac model and chip (Apple menu > About This Mac), the macOS version,
whether it's managed (above), and a screenshot of anything unexpected. To capture SnipForge's log,
start it from Terminal: `~/Applications/SnipForge.app/Contents/MacOS/SnipForge` (or the
`/Applications` path) and copy what it prints.

1. **Download check.** In Terminal, `shasum -a 256 SnipForge-*.dmg` matches the line in
   `SHA256SUMS.txt`.
2. **Install and first launch.** Open the .dmg, drag SnipForge to Applications, open it. Note the
   exact message, then follow the Gatekeeper steps. Note whether Open Anyway was there, whether it
   asked for an administrator, and whether anything else (an IT tool) blocked it.
3. **Library.** SnipForge opens to its Library, with an icon in the menu bar and the Dock. Click the
   menu bar icon: its menu opens (it shouldn't also start a snip). Close the Library, then open
   SnipForge again from Applications: note what happens (expected for now: no second copy starts,
   but the Library may not come back; use the menu bar icon > Library).
4. **Screen Recording.** Menu bar icon > New snip. macOS asks for Screen Recording: allow it, quit
   SnipForge (menu bar icon > Quit), open it again and snip. Note whether the frozen screen shows your
   other apps' windows (not just the desktop) and whether it's sharp on a Retina screen.
5. **Snipping.** Snip an area, then a full screen (from the toolbar at the top). With two monitors,
   snip on each. Esc cancels.
6. **Editor.** Draw an arrow, a box, text, a highlight, blur something, crop, undo and redo, then
   Copy and paste into Outlook or Teams; Save; Drag out into a Finder window and into an email; Pin.
7. **Library again.** The new snip is there with a thumbnail. Open it, star it, tag it, search for
   its tag, delete it (it goes to the Trash; the Trash button opens it), Open library folder (Finder).
8. **Share.** Send in Teams; Email (expected for now: the snip is copied and your mail app opens);
   Save and show in folder (Finder opens with the file selected).
9. **Settings.** Every page opens. Turn on Start when I sign in (expected for now: it says it isn't
   available yet). Switch Light, Dark and System, and an accent; change macOS's appearance with
   System on.
10. **Expected not to work yet:** a keyboard shortcut, clicking a window to snip just that window,
    Grab Text, finding words inside snips, Share > OneDrive or the share sheet, the mouse pointer in
    snips, scrolling capture, installing an update. Note anything else that fails.

## Phases, one PR each

Every phase keeps Windows behavior identical (the Windows CI and the desktop suite stay green), adds
unit tests that run on both platforms, and makes the matching self-test check required on macOS.
Mac-only code that needs a real Mac to prove it gets a manual check from the test plan.

0. **Groundwork** (this PR): Windows-only code behind platform checks, Windows-only dependencies
   marked, `windows` test marker, `.app` and `.dmg` build (`scripts/build_mac.py`), the manual
   macOS workflow, this plan.
1. **A Mac app people can try:** Screen Recording check and explanation before snipping; Mac
   wording (Trash, menu bar, ⌘, About); the Library on reopen; Settings > Open folder through Finder; license files and
   theme packs found in `Contents/Resources`; caches in `~/Library/Caches`; Dock icon or menu bar
   only, decided after the first tester. Attach the .dmg to preview releases (a macOS job in
   `preview.yml`) so testers can download it without a GitHub account.
2. **Window picking** with `CGWindowListCopyWindowInfo` (`_winrects_mac.py`), so clicking a window
   snips it and the Library shows where each snip came from.
3. **Keyboard shortcut** with `RegisterEventHotKey` (`_hotkey_mac.py`), with the Mac shortcuts in
   Settings. Like the Windows hook, it only ever acts on the shortcuts the person picked.
4. **OCR** with Vision (`_ocr_mac.py`): Grab Text, Library search and the redaction suggestions
   that use it.
5. **Share** with `NSSharingServicePicker`, email through the compose-email service, and OneDrive
   through its sync folder.
6. **Start at sign-in** with `SMAppService` (or a LaunchAgent).
7. **Pointer in snips and scrolling capture** (after the capture session's Windows versions land),
   including the Accessibility check.
8. **Updates and releases:** the .dmg install path in `update_platform.py`, the Release workflow
   building and attaching `SnipForge-<version>.dmg` and its checksum, `releases.json` macOS assets,
   managed preferences for policy, and a "Download for Mac" button on the download page.
9. **Optional, needs Josh:** a stable signing identity (self-signed certificate as a secret) so
   permissions survive updates.

## Building it

- **CI:** Actions > **macOS build** > Run workflow (optionally with a version such as
  `0.9.0-preview.1`). It never runs on pull requests, so it can't block the required checks.
- **On a Mac:** Python 3.14 from python.org (universal2), then
  `python -m pip install -r requirements-dev.txt` and `python scripts/build_mac.py`. Output:
  `dist/SnipForge.app` and `dist/SnipForge-<version>.dmg`. Run the packaged self-test with
  `dist/SnipForge.app/Contents/MacOS/SnipForge --self-test report.json`.
- The bundle identifier is `io.github.nighthawk909.snipforge` (`packaging/snipforge.spec`). Never
  change it once Macs have SnipForge: macOS keys the Screen Recording permission, settings and IT
  profiles to it, like the installer's `AppId` on Windows.
