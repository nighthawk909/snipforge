# Windows and Mac parity

Every feature, and where it stands on each platform. The goal is a ✅ in both columns; until then
a feature that isn't ready on one platform steps aside there (hidden, or explained), never fails.
Keep this page true: a PR that changes a feature's status on either platform updates its row
([CROSS-PLATFORM.md](CROSS-PLATFORM.md)). The macOS APIs and the phase each row belongs to are in
[MAC-PLAN.md](MAC-PLAN.md).

✅ works and is tested · 🟡 same code as Windows, expected to work, not yet checked on a real Mac ·
❌ not built yet (steps aside) · 🔒 needs something only the maintainer can decide

| Area | Feature | Windows | Mac | Mac plan |
|---|---|---|---|---|
| **Release** | Installer / download | ✅ setup.exe, portable zip | ✅ every release attaches `SnipForge-<version>.dmg` (#80) | |
| | Download page offers the right build | ✅ | ✅ Download for Mac (#80) | |
| | Updates (check, download, install, roll back) | ✅ | 🟡 checks, then says to download the .dmg and drag it to Applications | Phase 8: `.dmg` install path |
| | First launch without warnings | 🔒 SmartScreen warns until the installer is signed | 🔒 Gatekeeper's Open Anyway until notarized | Free signing (SignPath) / Apple account: maintainer's call |
| | IT policy settings | ✅ registry | ❌ | Phase 8: managed preferences |
| **App** | Starts, one copy at a time | ✅ | ✅ CI | |
| | Tray / menu bar icon and menu | ✅ | 🟡 menu bar wording done | Dock icon or menu bar only: after the first tester |
| | Opening it again brings up the Library | ✅ | 🟡 from Finder or the Dock (`reopen.py`) | Phase 1 done; a tester confirms |
| | Words and shortcuts (Trash, menu bar, Finder, ⌘, About) | ✅ | ✅ unit tests on both; CI compares with Qt's own Mac shortcut text | |
| | Fonts (no Segoe UI or Consolas on a Mac) | ✅ | ✅ CI self-test resolves the system font and Menlo | |
| | License texts and theme packs shipped with the app; caches | ✅ next to SnipForge.exe; `%LOCALAPPDATA%` | ✅ CI self-test finds them in `Contents/Resources`; `~/Library/Caches` | |
| | Start when I sign in | ✅ | ❌ explains it isn't available | Phase 6: `SMAppService` |
| | Settings, themes, accents | ✅ | 🟡 Match macOS; no Windows key switches | |
| | Report a problem (logs, versions, displays, last crash) | ✅ | 🟡 logs in `~/Library/Logs/SnipForge`, macOS version from `platform.mac_ver()` | |
| **Capture** | The snip key (Win+Shift+S, Print Screen) | ✅ | ❌ | Phase 3: `RegisterEventHotKey` (e.g. ⌘⇧2) |
| | Shortcuts of your own | ✅ | ❌ hidden | Phase 3: same API |
| | Capture presets (toolbar, menu bar, Settings; effects; editor, copy or folder) | ✅ | 🟡 same code; a preset's own shortcut is hidden with the other shortcuts | Phase 3: shortcuts |
| | Screen Recording permission check | n/a | 🟡 CI calls the check in the packaged app; the explanation and System Settings need a person | Phase 1 done |
| | Area and Full screen | ✅ | 🟡 needs Screen Recording (asked for first) | A tester confirms |
| | Window (click a window), and where a snip came from | ✅ | ❌ | Phase 2: `CGWindowListCopyWindowInfo` |
| | Fixed size | ✅ | 🟡 | |
| | Scrolling | ✅ | ❌ hidden | Phase 7: `CGEvent` scrolling (needs Accessibility) |
| | Delay countdown | ✅ | 🟡 (not yet kept out of the snip) | Phase 7: `sharingType` |
| | Mouse pointer in snips | ✅ | ❌ | Phase 7: `NSCursor` |
| | Magnifier | ✅ | 🟡 | |
| | Repeat last snip | ✅ | 🟡 | |
| | Private-window titles left out | ✅ | ❌ no titles at all yet | With Phase 2 |
| **Editor** | Markup tools, crop, undo, Beautify, quick styles | ✅ | ✅ CI self-test draws and renders | |
| | Rotate, eyedropper, Magnify, edges, Cut out, Smart erase, stamp | ✅ | 🟡 | |
| | Grab text, redaction suggestions | ✅ | ❌ | Phase 4: Vision |
| | Edit text (retype a line in the snip) | ✅ | ❌ explains, as Grab text does | Phase 4: Vision |
| | Smart Move | ✅ | 🟡 same code | |
| | Save PNG, JPG, WebP, PDF | ✅ | 🟡 | |
| | Copy, Drag out, Pin | ✅ | 🟡 | |
| | Copy, cut and paste markup between snips; right-click menu | ✅ | 🟡 Qt's clipboard and menus; the private markup format needs a check on a real Mac | A tester confirms |
| **Share** | Send in Teams | ✅ the share window with the snip attached; a chat by email for work accounts | 🟡 a chat by email (`msteams:`), the snip copied to paste | Phase 5: Teams through `NSSharingServicePicker` |
| | Email | ✅ Outlook draft with the snip | 🟡 copies, opens the mail app | Phase 5: compose-email service |
| | OneDrive link, share sheet | ✅ | ❌ hidden | Phase 5: `NSSharingServicePicker` |
| **Library** | Browse, favorites, tags, delete, search titles and tags | ✅ | 🟡 Trash (⌘⌫), Finder | |
| | Search the words inside snips | ✅ | ❌ | Phase 4: Vision |
| **Recording** | Record an area (screen, pointer effects) | ✅ | ❌ hidden | New phase: ScreenCaptureKit |
| | Microphone and computer sound | ✅ | ❌ | New phase: ScreenCaptureKit audio, AVFoundation |
| | Webcam bubble | ✅ | ❌ | New phase: Qt camera (AVFoundation) |
| | Video editor (trim, GIF) and recordings in the Library | ✅ | ❌ unverified | With the recording phase |
| | Finishes the file before sleep, shutdown or sign-out; keeps the computer awake; pauses when locked; says when the screen goes off | ✅ | ❌ | With the recording phase: `_power_mac.py` behind `power.py` (an IOPMAssertion; NSWorkspace's sleep, session and screen notifications) |
| | Follows the default sound device when it changes mid-recording (headset plugged in) | ✅ | ❌ | With the recording phase: AVFoundation's default-device notifications (microphone), ScreenCaptureKit (computer sound) |
