# SnipForge roadmap

Where SnipForge is going and how far it's got. Every PR that finishes something ticks its box here
(the main session keeps this file; other sessions say which box their PR ticks).

**The plan:** finish the Windows version first, then port everything to the Mac in one go. Until
then the Mac version must keep building and starting (CI checks every PR), Windows code keeps its
operating-system calls behind the platform layer so the port stays mechanical, and features that
aren't on the Mac yet step aside there ([CROSS-PLATFORM.md](CROSS-PLATFORM.md),
[PARITY.md](PARITY.md)).

## Windows

### Phase 1: the look, Settings and updates ✅
- [x] One-click download, download page and company update feeds (#9)
- [x] Releases publish only once their installer is attached (#11)
- [x] Update engine: automatic, ask first or by hand; Stable or Preview; any version, roll back (#13)
- [x] A new look: light and dark themes, accents, a properties panel and quick styles (#15)
- [x] The Library: search inside snips, favorites, tags, filters (#19)
- [x] The Settings window (#22)

### Phase 2: capture ✅
- [x] Capture toolbar: Area, Window, Full screen (#25)
- [x] Delay countdown and the mouse pointer in snips (#28)
- [x] Scrolling capture (#31)
- [x] Settings › Capture: mode, delay and pointer (#35)
- [x] Fixed size (#40)
- [x] Repeat last snip, and shortcuts of your own (#45, #53, #70)

### Phase 3: the editor ✅
- [x] Rotate, flip and resize (#51)
- [x] Eyedropper (#52)
- [x] Saved quick styles (#54)
- [x] Magnify (#58)
- [x] Border, torn edges and Cut out (#59)
- [x] "App name and date" stamp (#62)
- [x] Smart erase (#64)
- [x] Save as WebP or PDF (#67)
- [x] Polish: keyboard eyedropper, lens edges, torn-edge preview (#71)

### Phase 4: screen recording ✅
- [x] Qt Multimedia and FFmpeg bundled (#36)
- [x] Trim and copy without re-encoding; read frames (#41)
- [x] Screen area capture, pointer, highlight and click rings (#42)
- [x] Microphone and computer sound (#43)
- [x] The recorder engine (#44), in a process of its own (#50)
- [x] Recording UI: Record on the toolbar, ready panel, countdown, control bar (#55)
- [x] Webcam bubble (#57)
- [x] Video editor: trim and GIF (#47); recordings in the Library (#60)
- [x] Hardening: disk space (#48), keep-awake and lock (#61), one-hour soak test (#73), sound
  follows the default device (#74), power and lock behind the platform layer (#78), reliable
  timing tests (#79), what's left for a person to check (#88)
- [ ] Checked by hand on real hardware (the list in [VIDEO-PLAN.md](VIDEO-PLAN.md))

### Across the app
- [x] Privacy: private-window titles never kept, in many browsers and languages (#65, #70, #77)
- [x] Windows and Mac parity rules, tracker and skill (#75)
- [x] The Mac version is built, installed and started for every PR (#76)
- [ ] Every release includes the Mac version, with a Mac download on the page (#80)
- [x] Code-signing pipeline ready (#24); waiting on signing itself (below)

## Releases
- [x] 0.4.0 to 0.10.0: the update engine, the new look, Settings, capture parity
- [x] 0.11.0: Phase 3, recording, privacy
- [ ] 0.11.1: polish, the crash fix (#77), the first release with a Mac .dmg (#72, after #80)
- [ ] 1.0: when Phase 4's hand checks and the fix-it backlog are done

## Fix-it: feedback from testing
Josh tests the installed app and reports what's broken or could be better; the fix-it session
turns each item into an issue labelled `feedback`, a tested fix and a PR.
- [Open feedback](https://github.com/nighthawk909/snipforge/issues?q=is%3Aissue+is%3Aopen+label%3Afeedback)
  · [Fixed](https://github.com/nighthawk909/snipforge/issues?q=is%3Aissue+is%3Aclosed+label%3Afeedback)
  · [Bigger ideas for later](https://github.com/nighthawk909/snipforge/issues?q=is%3Aissue+label%3Aidea)

## Waiting on the maintainer
- [ ] Free code signing (SignPath for open source), so Windows stops warning on install
- [ ] A default "Start recording" shortcut: Win+Shift+R would mean widening the keyboard hook's rule
- [ ] Make the macOS CI check required for merging
- [ ] Offering SnipForge through Software Center or a company share
- [ ] A coworker with a Mac to run the [Mac test plan](MAC-PLAN.md#test-plan-for-a-coworker-with-a-mac)

## Mac port (after Windows)
The macOS API for each step is in [MAC-PLAN.md](MAC-PLAN.md); [PARITY.md](PARITY.md) tracks every
feature. Today the Mac version has the shared core (editor, Library, Settings) and none of the
operating-system pieces.
- [ ] Phase 1: an app people can try: Screen Recording check, Mac wording and fonts, back when
  reopened (#90)
- [ ] Phase 2: click a window to snip it, and where each snip came from
- [ ] Phase 3: the snip shortcut and shortcuts of your own
- [ ] Phase 4: Grab text and search inside snips (Vision)
- [ ] Phase 5: Share: the share sheet, email, OneDrive
- [ ] Phase 6: start at sign-in
- [ ] Phase 7: the pointer in snips, the countdown, scrolling capture
- [ ] Phase 8: updates that install themselves, and IT policy
- [ ] Phase 9: recording: screen and computer sound (ScreenCaptureKit), microphone, webcam
- [ ] A Mac tester runs the test plan
