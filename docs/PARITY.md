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
| **Release** | Installer / download | ✅ setup.exe, portable zip | ❌ `.dmg` built by CI but not attached to releases | Every release attaches the `.dmg` (next) |
| | Download page offers the right build | ✅ | ❌ | With the release work |
| | Updates (check, download, install, roll back) | ✅ | ❌ | Phase 8: `.dmg` install path |
| | First launch without warnings | 🔒 SmartScreen warns until the installer is signed | 🔒 Gatekeeper's Open Anyway until notarized | Free signing (SignPath) / Apple account: maintainer's call |
| | IT policy settings | ✅ registry | ❌ | Phase 8: managed preferences |
| **App** | Starts, one copy at a time | ✅ | ✅ CI | |
| | Tray / menu bar icon and menu | ✅ | 🟡 | Phase 1 |
| | Opening it again brings up the Library | ✅ | ❌ | Phase 1 |
| | Start when I sign in | ✅ | ❌ explains it isn't available | Phase 6: `SMAppService` |
| | Settings, themes, accents | ✅ | 🟡 | |
| **Capture** | The snip key (Win+Shift+S, Print Screen) | ✅ | ❌ | Phase 3: `RegisterEventHotKey` (e.g. ⌘⇧2) |
| | Shortcuts of your own | ✅ | ❌ hidden | Phase 3: same API |
| | Screen Recording permission check | n/a | ❌ | Phase 1 |
| | Area and Full screen | ✅ | 🟡 needs Screen Recording | Phase 1 |
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
| | Save PNG, JPG, WebP, PDF | ✅ | 🟡 | |
| | Copy, Drag out, Pin | ✅ | 🟡 | |
| **Share** | Send in Teams | ✅ | 🟡 | |
| | Email | ✅ Outlook draft with the snip | 🟡 copies, opens the mail app | Phase 5: compose-email service |
| | OneDrive link, share sheet | ✅ | ❌ | Phase 5: `NSSharingServicePicker` |
| **Library** | Browse, favorites, tags, delete, search titles and tags | ✅ | 🟡 | |
| | Search the words inside snips | ✅ | ❌ | Phase 4: Vision |
| **Recording** | Record an area (screen, pointer effects) | ✅ | ❌ hidden | New phase: ScreenCaptureKit |
| | Microphone and computer sound | ✅ | ❌ | New phase: ScreenCaptureKit audio, AVFoundation |
| | Webcam bubble | ✅ | ❌ | New phase: Qt camera (AVFoundation) |
| | Video editor (trim, GIF) and recordings in the Library | ✅ | ❌ unverified | With the recording phase |
