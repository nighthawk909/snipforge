# SnipForge video recording: the plan (Phase 4)

Recording is the feature that "cannot miss", so this plan comes with numbers measured on a real
PC and a working prototype (`prototypes/video/` in draft pull request #34, branch
`explore/video-recording`, kept for reference and not merged). The design was approved on
2026-10-17 with every recommendation under [Decisions](#decisions); the [build plan](#build-plan)
says which parts exist so far.

**The short version.** Capture the screen ourselves with Windows' DXGI Desktop Duplication, copying
only the chosen area. Capture the microphone and system audio ourselves with WASAPI. Draw the
pointer, click rings and webcam into each frame with QPainter. Then hand frames and mixed audio to
**Qt Multimedia's own recorder**, which encodes H.264 with Windows' built-in encoder and AAC with
the LGPL FFmpeg that Qt ships, into MP4 (how a recording survives a crash is settled in the
recorder step; see [Risks](#risks)). Trim and GIF use those same FFmpeg libraries. No new Python packages for recording
(Pillow for GIF is the one addition), no paid anything, no network, no drivers, no admin, about
**+6.8 MB on the installer**, and nothing loaded at startup.

The prototype records 10 s of a screen area with microphone and system audio to MP4 on the test
PC. Sound and picture land where they happened: the only gap measured (55-105 ms) is the time
Windows itself took to play the test beep (details below).

## Contents

- [How it fits together](#how-it-fits-together)
- [What the prototype proved](#what-the-prototype-proved)
- [The options, and why these](#the-options-and-why-these)
- [Each requirement, and how it's met](#each-requirement-and-how-its-met)
- [Installer size and startup](#installer-size-and-startup)
- [Licenses and patents](#licenses-and-patents)
- [IT review](#it-review)
- [Mac outlook](#mac-outlook)
- [Risks](#risks)
- [Build plan](#build-plan)
- [Decisions](#decisions)

## How it fits together

```
 Choose: the existing freeze-frame overlay (capture.py), in a new "Video" mode
   │ area / window / screen → a rectangle in physical pixels on one monitor
   ▼
 ┌──────────────── recorder (its own process: SnipForge.exe --record) ───────────────────┐
 │ DXGI Desktop Duplication ─ copies just the area on the GPU ─┐                           │
 │ pointer image + highlight + click rings (pointer.py)        ├─ QPainter ─► QVideoFrameInput ─┐
 │ webcam frame (QCamera → QVideoSink), picture-in-picture   ──┘                                 │
 │                                                                                               ├─► QMediaRecorder ─► .mp4
 │ WASAPI microphone ──┐                                                                         │   H.264: Windows' encoder (h264_mf)
 │ WASAPI loopback ────┴─ mixer: places audio by its timestamps ─► QAudioBufferInput ────────────┘   AAC: Qt's FFmpeg
 │ no windows: the control bar is SnipForge's, fed by progress (time, levels)            │
 └────────────────────────────────────────────────────────────────────────────────────────┘
   │ stop → Library item
   ▼
 Video editor: play, trim (stream copy, frame-exact), export MP4 or GIF, share
```

Everything runs on one clock: Windows' QueryPerformanceCounter, which is what Python's
`time.perf_counter()`, WASAPI's packet timestamps and DXGI all use. That is what keeps sound and
picture together, through pauses, stalls and dropped frames.

## What the prototype proved

Measured on the maintainer's PC: Intel i5-12500H (12 cores, 16 threads), RTX 3060 Laptop + Intel UHD, one
3440×1440 60 Hz monitor at 100% scaling, Windows 11 (build 26300), Python 3.14.2, PySide6 6.12.0.
Other work was running at the same time (the CPU was 68-76% busy before each test), so these are
pessimistic numbers. Every recording was checked by decoding it again with Qt.

| What | Result |
|---|---|
| **10 s of a 1280×720 area, mic + system audio, 30 fps** (the task) | MP4, H.264 + AAC 48 kHz stereo, 3.6 MB. 292-296 of 300 frames (the few missing are mostly while the recorder starts). 1.2-1.6 CPU cores. |
| **Sound and picture together** (a box flashes and a beep plays at 2, 5 and 8 s; the file is checked for both) | Beep 55-105 ms after the flash, the same every time and through pauses. Measured from the moment each was triggered: picture 27-67 ms to the file (one frame plus the display), sound 111-131 ms (the beep's own trip through Windows' audio output). So the recorder adds no offset of its own. |
| 1920×1080 area, 30 fps | 292 of 300 frames, ~2.2 cores, sound 63-67 ms after picture |
| 1280×720, 60 fps | 552 of 600 frames, ~1.9 cores, 77-100 ms |
| Whole 3440×1440 screen, 30 fps, full size | The encoder keeps up with only ~22 fps (222 of 300 frames). The file is still exactly 10 s and in sync: missing frames are dropped, not queued. |
| Whole 3440×1440 screen, scaled to 1920 wide | 292 of 300 frames, ~2.1 cores. **So: cap the output at 1080p by default.** |
| Pause at 3 s, resume 1.5 s later | 11.7 s of recording made a 10.0 s file; the flashes and beeps after the pause still matched (62-82 ms). |
| Stalls | Some early runs dropped up to 84 frames and lost up to 2.6 s of audio. Mostly the prototype's own fault: its control bar restyled itself 100 times a second, which froze Qt's main thread for up to 1.2 s, and through Python's GIL the capture threads with it. After fixing that, 6 of 7 runs dropped 5-8 frames; one still hit a 2.3 s stall on the busy PC (cause not found). Sound and picture stayed together through every stall, because timestamps, not counting, decide where everything goes. The lesson is built into the design: the recorder gets its own process and its UI does very little per tick (see [Risks](#risks)). |
| Control bar placed *inside* the recorded area, in magenta | Never appears in any frame: `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` works with DXGI. |
| Pointer, highlight, click rings | Drawn correctly; both test clicks became rings (clicks read by polling, no hook). |
| Choosing with SnipForge's own overlay (`--choose`) | Returns the area in physical pixels; Enter gives the whole monitor. |
| A crash while recording | MP4: the file is unreadable, everything lost. MKV: recoverable, but see the correction below: Qt writes MPEG-4 Part 2 into MKV, not H.264. AVI: a 5 s recording killed mid-way was readable H.264 + AAC (103 MB written), but Qt stamps the first video frame wrong (fixable when copying into MP4). **Open: settled in the recorder step.** |
| Trim (cut 4.5-8.5 s of a 10 s 1080p recording) | 15-38 ms, no re-encoding; the cut starts on exactly the right frame (flash at 5.067 s became 0.567 s) and the sound with it. |
| GIF (10 s of 1080p → 12 fps, 960 wide) | 2.6 s to make; 0.45 MB for a simple screen, 3.1 MB for a busy desktop. |
| Startup cost | None if Qt Multimedia is loaded only when recording starts. Then: import 20-36 ms, first recorder 0.29-0.37 s, audio devices 0.4-1.3 s, all hidden behind the 3-2-1 countdown. |

Things the prototype found out about Qt 6.12 that the design depends on:

- `QVideoFrameInput` honours each frame's timestamp (frames can be dropped without the video
  drifting), but measures from the first frame it gets, so frame 0 must be at 0.0 s.
- `QAudioBufferInput` ignores audio timestamps and plays buffers end to end. So the mixer must
  deliver one unbroken stream that starts at 0.0 s, with silence filled in. It does.
- Qt's encoder for H.264 is Windows' own **software** H.264 encoder ("H264 Encoder MFT",
  Constrained Baseline profile, no B-frames; keyframes between about 1 s apart on a busy screen and 3 s on a still one). Qt's FFmpeg has no NVIDIA/Intel/AMD
  hardware encoders, and Qt doesn't ask Media Foundation for its hardware ones. Fine at 1080p30;
  the reason for the 1080p cap.
- **Correction (found after the prototype, while building step 1):** Qt 6.12 offers no H.264 in
  Matroska (MKV), and when asked for it **silently encodes MPEG-4 Part 2 instead**, a codec browsers
  and Teams can't play. The prototype's later runs, which recorded MKV and copied it into MP4, were
  therefore MPEG-4 Part 2; its earlier runs straight to MP4 were H.264 and gave the same timings.
  Qt's MKV also starts at ~1 s with a stray audio packet at 0. The test suite now checks the codec
  of every clip it makes, so a silent fallback can't go unnoticed again.
- Qt's AVI output is H.264 + AAC and is written as it goes, so it survives a crash; but the first
  video frame is stamped 0 and every later one 656 frames (21.9 s) late, consistently.
- `QMediaRecorder.setOutputDevice` with a stream that can't seek still writes an ordinary MP4 (index
  at the end), so it gives no crash-safe MP4; and PySide6 can't pass binary data through a
  `QIODevice` written in Python (the bytes arrive as a truncated string).
- Qt's own `QScreenCapture` reads the whole monitor back every frame: 20-29 fps on this 3440×1440
  screen, whichever of its back-ends. Copying just the area on the GPU (DXGI) ran at the screen's
  own update rate (48-52 fps measured, limited by the test window).

## The options, and why these

### Capture

| Option | Verdict |
|---|---|
| **DXGI Desktop Duplication via ctypes** (chosen) | Fastest (copies only the area on the GPU), no dependency, no admin, no yellow border, honours excluded windows. Windows 10 1703 or later (for `DuplicateOutput1`, which Qt's per-monitor-DPI mode needs). ~200 lines of COM through ctypes. The prototype finds the right GPU on hybrid laptops and reopens the duplication when Windows drops it (UAC prompt, lock screen); that last path is written but wasn't triggered in these tests. |
| Windows.Graphics.Capture (via pywinrt, already used for OCR) | Can follow one window even when it's covered or moved, which DXGI can't. But it needs D3D11 interop through ctypes anyway, more winrt packages, and shows a yellow border on Windows 10. **Later**, for a "follow this window" option. |
| Qt `QScreenCapture` / `QWindowCapture` | Simplest, but whole-monitor only (crop afterwards) and 20-29 fps on a big screen. |
| FFmpeg `ddagrab` / `gdigrab` (ffmpeg.exe) | Means shipping an ffmpeg.exe (a second FFmpeg) and talking to it through pipes; can't capture system audio without a third-party driver (below). |

### Encoding

| Option | Verdict |
|---|---|
| **Qt Multimedia's `QMediaRecorder`, fed with `QVideoFrameInput` and `QAudioBufferInput`** (chosen) | Already part of PySide6; the same code runs on macOS. Uses Qt's bundled LGPL FFmpeg 9.0.1, which encodes H.264 with Windows' Media Foundation encoder and AAC with FFmpeg's encoder (or Windows' AAC encoder, below). Measured fast enough for 1080p30 and 720p60. |
| Media Foundation directly (`IMFSinkWriter` via ctypes) | Can use the graphics card's hardware encoder and High profile. Windows-only and several hundred lines of COM. **Kept in reserve** behind the same encoder interface, if coworkers' laptops struggle at 1080p. |
| A bundled ffmpeg.exe (LGPL build) | +30-80 MB, a second copy of FFmpeg, a process to manage, and nobody to build it but us. Not needed: Qt's FFmpeg libraries do the same jobs. |

### Audio

| Option | Verdict |
|---|---|
| **WASAPI via ctypes** (chosen) | Built into Windows. Records the microphone *and* "what you hear" (loopback) with no driver, converting both to 48 kHz stereo for us, and stamps each packet with the same clock as the video. ~150 lines. |
| `soundcard` (BSD) | Does loopback, but needs numpy and cffi (+15-30 MB) and gives no timestamps. |
| `sounddevice` (MIT, PortAudio) | Its bundled PortAudio doesn't do WASAPI loopback. |
| `PyAudioWPatch` (MIT) | Does loopback; a one-person fork of PyAudio, so a maintenance and supply-chain risk. |
| `pycaw` | Volume and mute control only; can't record. (The prototype does its own mute check.) |
| FFmpeg `dshow` | Microphones only. System audio needs a virtual audio driver installed with admin rights: an IT no-go. |
| Qt `QAudioSource` | Microphone only (Qt 6.12 has no loopback device) and no device timestamps. One audio input per session, so no mixing either. |

### Webcam

**Qt's `QCamera`** (Media Foundation on Windows, AVFoundation on macOS) into a `QVideoSink`; each
camera frame is drawn into the recording as a rounded picture-in-picture. No extra dependency. The
prototype has it (`--webcam`), but **it wasn't run on the test PC**, so as not to switch on the
maintainer's camera without asking; it's one line to try (see the prototype README).

### Can Qt 6.12 do it all by itself?

Partly. `QMediaCaptureSession` with `QScreenCapture`, `QAudioInput`, `QCamera` and `QMediaRecorder`
records the screen and the microphone in about 20 lines. But it records only one audio input (no
system audio, no mixing), one video source (no webcam picture-in-picture, no click rings), the whole
monitor (no area) at 20-29 fps on a large screen, and has no trim. So Qt does the parts it's best
at (encoding, the camera, playback), and SnipForge does capture, audio and drawing.

## Each requirement, and how it's met

| Requirement | How |
|---|---|
| Area, window or full screen, chosen with the freeze-frame overlay | The capture toolbar gets a **Snip / Record** switch. `MODES` in `capture_toolbar.py` stays as it is (Area, Window, Full screen mean the same in both); each `CaptureMode` gains a `video_hint` for the bottom-of-screen text. `_ScreenOverlay` gains a `selected_rect` signal (the area in physical desktop pixels, plus the `WindowInfo` when a window was clicked) next to today's `selected(QImage)`. The prototype's `--choose` shows this works with the real overlay. A window recording records that window's rectangle (as Snagit does); following a moving window comes later (Windows.Graphics.Capture). One monitor per recording, as DXGI works per monitor. |
| Microphone, system audio, or both, with levels | Two WASAPI streams into the mixer, each with its own on/off and gain; the control bar and the ready panel show both levels; a muted or silent microphone is pointed out before recording starts. |
| Webcam picture-in-picture, movable | A round or rounded preview bubble on screen, draggable, hidden from the recording itself; where it sits relative to the area is where it's drawn into each frame. |
| Cursor highlight and click rings | `snipforge/pointer.py` (from #28, which already draws the real pointer shape into snips) plus a cache by pointer shape, so it's cheap at 30-60 fps. Highlight under the pointer, a ring per click (left and right in different colours). Clicks are read by polling the mouse buttons each frame, not with a hook. |
| Pause and resume | One clock with the paused time taken out; frames and audio that fall in a pause are dropped. Verified in the prototype. |
| MP4 (H.264/AAC) and GIF | MP4 from the recorder. GIF from the editor: frames decoded with Qt's FFmpeg, written with Pillow (a palette per frame, dithered, only the changed part of each frame stored). |
| Small floating control bar | Timer, pause/resume, stop, mic and system-audio toggles and levels, hidden from the recording with `WDA_EXCLUDEFROMCAPTURE`. A stop hotkey through `RegisterHotKey` (see [Decisions](#decisions)). |
| Trim in the editor | Stream copy with Qt's FFmpeg libraries; the start is frame-exact (the MP4 gets an edit list), no quality loss, milliseconds. |
| Startup and installer size | Recording loads nothing at startup; +6.8 MB installer (below). |
| No paid services or licenses, no network | All free and built into Windows or Qt. Recording never touches the network (below). |
| IT review | No new hook, driver, admin right or network use (below). |

## Installer size and startup

Measured by building SnipForge twice with the project's own PyInstaller spec, once with Qt
Multimedia and once without (the only difference), then compressing each the way the installer does
(LZMA2 with the x86 filter, solid). The same method gives 30.95 MB for today's build against the real
29.42 MB `SnipForge-0.8.0-setup.exe`, so it slightly overstates.

| | Today | With video | Difference |
|---|---|---|---|
| Installed size | 115.0 MB | 138.3 MB | +23.3 MB |
| Installer (estimated from the compressed build) | 29.4 MB | ~36.2 MB | **+6.8 MB (+23%)** |
| Portable zip | 48.3 MB | 58.8 MB | +10.5 MB |

Where it goes (compressed): FFmpeg's `avcodec` 4.8 MB, `avformat` 1.1 MB, `Qt6Multimedia` 0.5 MB,
`avutil` 0.4 MB, `swscale` 0.3 MB, Qt's FFmpeg plugin 0.3 MB, the rest under 0.2 MB each. Leaving out
what recording doesn't use (`QtMultimediaWidgets`, Qt's second media back-end
`windowsmediaplugin.dll`, translations) saves ~0.3 MB. Pillow for GIF adds ~0.9 MB if only its core
and GIF writer are bundled. QtMultimedia comes from the `PySide6-Addons` wheel, which is large to
download in CI but only the files above are packaged.

Startup: today's spec leaves Qt Multimedia out entirely, and the plan keeps it out of the main
process: only the recorder process imports it. The first recording after starting SnipForge costs
~0.3 s to load the recorder and 0.4-1.3 s to open the audio devices, during the countdown.

## Licenses and patents

| Component | License | What it asks of us |
|---|---|---|
| Qt Multimedia (`Qt6Multimedia.dll`, `QtMultimedia.pyd`, plugins) | LGPL-3.0, like the Qt modules already shipped | Nothing new: unmodified, dynamically linked separate files, license texts and source links in `THIRD_PARTY_NOTICES.md`. |
| FFmpeg 9.0.1 libraries (`avcodec-63.dll`, `avformat-63.dll`, `avutil-61.dll`, `swscale-10.dll`, `swresample-7.dll`), as built by Qt | **LGPL-2.1-or-later** (checked: the libraries report "LGPL version 2.1 or later", built without `--enable-gpl` or `--enable-nonfree`) | Ship `licenses/LGPL-2.1.txt`; name FFmpeg, its version and where to get its source (ffmpeg.org, tag `n9.0.1`) and Qt's build options in `THIRD_PARTY_NOTICES.md` and Settings › About; keep them as separate, unmodified DLLs people could replace (PyInstaller's one-folder build already does). Calling them through ctypes is dynamic linking. |
| Windows Media Foundation (H.264 and AAC encoders), WASAPI, DXGI | Part of Windows | Nothing. |
| Pillow (GIF writing) | MIT-CMU (HPND), permissive | Include its license text. |

**Patents.** H.264 and AAC are covered by patent pools, and Qt's documentation warns that royalties
can apply to any implementation, including one the operating system provides. In practice SnipForge
encodes H.264 with the encoder Microsoft licenses as part of Windows. Audio can go the same way: setting
`QT_FFMPEG_EXCLUDE_ENCODERS=aac` (checked) makes Qt use Windows' "Microsoft AAC Audio Encoder MFT"
instead of FFmpeg's (its bitrate setting still needs checking). That leaves FFmpeg's H.264 *decoder*
(GIF export, thumbnails), which ships in Qt's standard build. This is the same position as any free
app that records MP4 on Windows; noted here in case a company's legal team asks. It isn't legal advice.

## IT review

What `docs/IT-REVIEW.md` gains when recording ships (none of it needs admin rights):

- **Screen**: read only while a recording the person started is running, with DXGI Desktop
  Duplication (the API Teams and OBS use). DRM-protected video shows black, as Windows decides.
- **Microphone and camera**: Windows' own privacy settings apply ("Let desktop apps access your
  microphone/camera"); Windows shows its microphone-in-use icon and the camera light while recording,
  and SnipForge appears in Settings › Privacy › Microphone/Camera recent activity.
- **System audio**: Windows' WASAPI loopback, the documented way to record what the speakers play.
  No virtual audio driver.
- **No new hook.** Click rings come from reading the mouse buttons' state (`GetAsyncKeyState`) once
  per frame, only while recording, only if click rings are on. The keyboard hook is unchanged.
- **Windows hidden from the recording**: SnipForge's own control bar and webcam bubble use
  `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`, only on its own windows.
- **A second process**: while recording, `SnipForge.exe --record` runs next to SnipForge.
- **Files**: the recording is written where the finished `.mp4` goes (the Library); if the recorder
  step settles on a crash-safe intermediate file, it's written there too and deleted afterwards.
- **Network: none.** Qt's FFmpeg build includes network protocols (`--enable-network`), but
  SnipForge only ever gives it local file paths and limits it to the `file` protocol
  (`protocol_whitelist`, and `QT_FFMPEG_PROTOCOL_WHITELIST=file` for Qt's own player).

## Mac outlook

The choices above were made so the Mac version reuses most of the work
(`docs/MAC-PLAN.md`, in the Mac groundwork pull request #32, covers the rest of the Mac port).

| Part | Windows | macOS | Shared? |
|---|---|---|---|
| Choosing the area | freeze-frame overlay | the same overlay | Yes |
| Screen frames | DXGI Desktop Duplication | ScreenCaptureKit `SCStream` through PyObjC (MIT), with `sourceRect` for the area, `showsCursor = false` so we draw the pointer, our own windows excluded with `SCContentFilter` | No (one module each) |
| System audio | WASAPI loopback | the same `SCStream` (`capturesAudio`, macOS 13+) | No |
| Microphone | WASAPI | `SCStream` `captureMicrophone` (macOS 15+), or `QAudioSource` / AVFoundation on macOS 14 | No |
| Pointer and clicks | `GetCursorInfo`, `GetAsyncKeyState` | `NSCursor` image, `CGEventSourceButtonState` (polling, no permission) | No (behind `pointer.py`) |
| Mixer, compositor, control bar, webcam (QCamera), pause | | | **Yes** |
| Encoding | Qt `QMediaRecorder` | Qt `QMediaRecorder`: confirmed by the macOS build workflow (the packaged app's self-test records H.264 + AAC on Apple silicon and on Intel under Rosetta; Qt's Mac build is the same FFmpeg 9.0.1 LGPL). Fallback if ever needed: `AVAssetWriter` through PyObjC | **Yes** |
| Trim, GIF, editor | Qt's FFmpeg via ctypes | the same, loading `libavformat.63.dylib` and so on from `PySide6/Qt/lib` (works in the macOS build workflow) | **Yes** |

Permissions on the Mac: Screen & System Audio Recording (already needed for snips), Microphone and
Camera (usage descriptions in `Info.plist`). Without the paid Apple account the app is ad-hoc
signed, so, as MAC-PLAN.md explains, macOS may ask for these again after an update.

That is also why the Windows-only shortcuts weren't taken: Media Foundation's `IMFSinkWriter` for
encoding, or a Windows-only ffmpeg.exe, would each be a second encoder to write for the Mac.

## Risks

| Risk | What the plan does |
|---|---|
| **Python stalls starve the capture threads** (a slow UI update in the prototype froze the main thread for up to 1.2 s and cost 2.6 s of audio) | Record in a separate process, so the editor, Library indexing and OCR can't stall it; keep its UI work tiny (update the control bar ten times a second, only what changed); 1 s WASAPI buffers instead of 0.2 s; register the audio threads with Windows' multimedia scheduler (MMCSS) and raise the recorder's priority; keep per-frame Python work small (the slowest frame took 4-17 ms to make, against 33 ms available). A desktop test fails if a 10 s recording drops more than 3% of frames. **Found in step 6:** a styled control bar drawn in the recorder process stalled its frame thread for 260 ms here and starved it almost completely on GitHub's runners, so the recorder process shows no windows at all (the bar is SnipForge's), and screen watching and frame making run on separate threads. |
| CPU on older laptops (Windows' software H.264 encoder) | Default output capped at 1080p / 30 fps; frames dropped, never queued, so the file stays in sync; the control bar warns when frames are being dropped. If field testing shows trouble, add the Media Foundation hardware encoder behind the encoder interface. |
| Crash or power loss mid-recording | **Decided in the recorder step: MP4 written directly.** Tested the alternative, AVI (H.264, written as it goes): Qt flushes it 256 KB at a time, so a recording killed at 4 s was unreadable and one killed at 9 s lost its first frame and its last second, and still screens compress so well that 256 KB can be 10-20 s; its timestamps also need repairing. Instead the recorder runs in its own process (a crash in the rest of SnipForge can't touch it) and finishes the file when Windows announces sleep, shutdown or sign-out, which covers a laptop battery running out. A hard power cut or the recorder itself crashing still loses the recording. |
| Qt's recorder and player now and then misbehaved in the test runner: Qt's player sometimes loaded a fresh clip with no tracks or stalled before its first frame, and in about 1 run in 65 a clip read back as a different format | Files are checked with FFmpeg directly, not Qt's player (the editor step must test the player the same way). After recorders were made to always shut down and let go of their file, the stress test (`pytest -m stress`: 200 recordings, every file checked) passed four times in a row, 800 recordings, none bad; the recorder also checks every real recording before reporting it finished. |
| On macOS CI, Qt 6.12 crashed (segfault) while deleting a recorder that had failed or been abandoned (the two tests of those paths are Windows-only for now) | Reproduce on a Mac in the Mac phase; the recorder process isolates such a crash from the rest of SnipForge either way. |
| Windows "N" editions (sold in Europe) ship without Media Foundation, so without the H.264 encoder | **Done (step 9, `support.py`):** the self-test and the recorder check for it; recording explains how to add the free Media Feature Pack instead of failing. |
| Audio clocks drift from the system clock (up to ~0.01-0.1%, up to a few seconds an hour) | The mixer (`snipforge/video/mixer.py`) keeps each source within 2 ms of its timestamps by adding one sample (the average of its neighbours) or dropping one, in the middle of a 10 ms packet: inaudible, and enough for drift up to 0.2%. Tested over a simulated minute both ways; a one-hour soak test in the hardening step. |
| A wrong audio timestamp (Windows flags some, virtual and remote-desktop devices can be off) | The mixer never trusts a stamp from the future or one that jumps backwards: the packet follows on from the last one, so one bad stamp can't silence a source. Packets that arrive after their time was handed to the encoder are counted (`late_frames`); the recorder keeps 0.25 s back for them. |
| Qt changes between releases (frame timestamps, container quirks, codec fallbacks, FFmpeg's DLL versions) | Tests pin each behaviour the design relies on (see the build plan); the ctypes layer refuses to run with an FFmpeg major version it wasn't tested with, and says so, instead of misbehaving. Dependabot's PySide6 updates run those tests. |
| Monitors: several, mixed scaling, rotated, HDR | One monitor per recording; physical pixels throughout; rotation handled when drawing; DXGI hands HDR screens over in normal colour (the prototype requests 8-bit BGRA). Each gets a manual check in the hardening PR. |
| Microphone muted or blocked by privacy settings | The ready panel shows the live level before recording and names the cause (Windows mute, privacy setting, no device). |
| UAC prompts, the lock screen, sleep, the screen turning off | DXGI reopens after a desktop switch; frames meanwhile repeat the last image. Locking pauses the recording (and says so); sleep and the session ending finish the file first; Windows is asked not to sleep or turn the screen off for inactivity while recording, and a screen turned off anyway is mentioned. |

## Build plan

Each step is one PR, with tests first as `CLAUDE.md` asks, and each leaves `main` releasable. The
recording UI appears in step 6; before that the pieces are tested on their own.

1. **Packaging groundwork** (done first). Pin `PySide6-Addons==6.12.0`; let the spec include Qt
   Multimedia, leaving out `QtMultimediaWidgets`, its other media back-end and the translations;
   FFmpeg in `THIRD_PARTY_NOTICES.md` and `licenses/LGPL-2.1.txt`; an installer size budget
   (40 MB) that fails the build. `snipforge/video/clip.py` encodes a one-second H.264 + AAC test
   clip the way recordings will be made, and `snipforge/video/ffmpeg.py` (the start of the media
   toolkit, brought forward) reads back exactly what's in it, through Qt's FFmpeg libraries, local
   files only. The packaged self-test runs both (no screen or devices needed, so it runs in CI).
2. **Media toolkit** (`snipforge/video/ffmpeg.py`, started in step 1 with probing). Copy
   AVI → MP4 if the recorder step chooses AVI (correcting Qt's timestamps), trim, a frame reader;
   the FFmpeg version gate. Tests
   make short clips with Qt and check durations, frames and sync after each operation.
3. **Audio** (`wasapi.py`, `mixer.py`). Microphone and loopback, device list and default, mute and
   privacy checks, MMCSS, the timeline mixer with drift correction, gain and levels. Unit tests feed
   the mixer packets with gaps, jitter, drift and pauses; a desktop test records a tone through
   loopback.
4. **Screen and pointer** (`dxgi.py`, `pointer.py` additions, compositor). Adapter and monitor
   lookup, desktop switches, rotation, protected content; pointer shape cache, highlight, click
   rings. Desktop test: records a test window's area and checks its pixels.
5. **Recorder engine** (`recorder.py`, `encoder.py`, `screen_frames.py`, `gdi.py`,
   `recording_check.py`). Frame clock on its own thread, the encoder's bounded queue (never dropping
   frame 0), pause, stop → MP4 checked with FFmpeg stream by stream; sound sources that can't open or
   get lost become warnings, and so does losing the screen mid-recording (what was recorded is
   kept). The clock starts only once Qt's encoder takes frames ("starting": ~0.1 s, 1.3 s on a
   saturated CPU, which used to cost the first second of video); starting and finishing each have a
   time limit; `cancel()` keeps nothing and `finish_now()` finishes the file there and then. Tests with
   a fake screen whose frames show their due time, a desktop test of the real thing (flash and beep
   in the file together, a window hidden from capture never in it), and the stress test.
   **5b. The recorder process** (`recorder_process.py`, `remote_recorder.py`, `channel.py`,
   `power.py`): `SnipForge.exe --record <channel>`, started detached for each recording. Its channel
   to SnipForge is JSON lines over a `QLocalSocket` with a random name only this user can open;
   `RemoteRecorder` has the `Recorder`'s controls and signals, and checks what comes back. A hidden
   window hears Windows' sleep (`PBT_APMSUSPEND`, ~2 s allowed) and session end
   (`WM_QUERYENDSESSION`/`WM_ENDSESSION`, ~5 s, with "Saving your screen recording" as the reason if
   Windows has to wait) and finishes the file before returning; a desktop test sends the real sleep
   message to the real process and finds the file whole when `SendMessage` returns. If SnipForge
   goes away, the recording is finished too (until step 6 puts the controls in the recorder
   process). The packaged self-test starts one and has it answer.
6. **Recording UI**. Snip / Record switch on the capture toolbar, `selected_rect`, the ready panel
   (sources, levels, webcam on/off), the countdown (reusing the snip delay's), the control bar,
   the stop hotkey, "Record video" in the tray menu.
   Built: Record on the capture toolbar (or R) hands the chosen area, in physical desktop pixels,
   to `record_flow.py`; the ready panel (`ready_panel.py`: microphone with its device, computer
   sound, live meters from `levels.py`, pointer effects, countdown; remembered in `video_prefs.py`)
   and a dashed outline (`area_frame.py`); the recorder process starts held while the countdown
   runs and begins as it ends (`Recorder.start(hold=True)`, `go()`), so the start isn't late by the
   process's own start-up; the control bar (`control_bar.py`) is SnipForge's, fed by the recorder's
   progress (time and levels, 4 a second): the recorder process shows nothing, because a styled
   window drawing there held Python's lock long enough to starve the frame thread (a 260 ms stall
   here, near-total on GitHub's runners). If SnipForge goes away, the recorder finishes and keeps
   the file. Watching the screen and making frames run on threads of their own, so a slow screen
   (a software graphics driver, a busy GPU) only makes the picture lag. "Record video" / "Stop
   recording" in the tray, and a "Record video, or stop recording" shortcut of your own (no
   combination until one is chosen); a finished recording opens in the video editor. Recordings are
   saved in the Library folder, one folder each (`recordings.py`), and listed there (step 8).
   The webcam's switch comes with step 7.
7. **Webcam**. Device choice, the movable bubble (shape and size), hidden from the recording, drawn
   into each frame.
   Built: the parts (#56: `webcam.py`, `webcam_bubble.py`, `webcam_layer.py`), then wired in. The
   ready panel's Camera switch (with its camera menu) shows the bubble beside the area while you get
   ready; it's remembered with its shape, size and place. All of it is SnipForge's
   (`flow_camera.py`): a window showing live video in the recorder process would starve its frame
   thread. The recorder gets the camera's newest picture through shared memory (`camera_share.py`,
   guarded by a sequence number so a picture being written isn't read; a camera that stops is dropped
   after 2 s, never frozen into the video) and the bubble's place over the channel ("bubble", as it's
   dragged), and draws the camera under the pointer (`CompositeOverlay`). A camera that can't be
   reached or drawn is left out with a warning; the recording goes on. The Camera switch is off each
   time until switched on. Recordings show
   the camera mirrored, as the bubble does (`mirror=False` in `WebcamLayer` changes it).
8. **Video editor and Library**. Recordings in the Library with a thumbnail and length; a player
   built on `QMediaPlayer` and `QVideoSink` (no `QtMultimediaWidgets`); a timeline with thumbnails;
   trim; export MP4 or GIF (adds Pillow); copy, share, drag out.
   Built: the editor (#47: player, timeline, trim, GIF). The Library lists a recording beside snips
   once it's finished: the recorder process writes `recording.json` (length and size) after the
   file is, so one still being recorded never shows. Its card is a frame from half a second in
   (saved as `preview.png` the first time), with a play badge and its length; opening it opens the
   video editor (the one already showing it, if there is one); Copy puts the file on the clipboard
   to paste into a chat, an email or a folder; favourites, tags, search ("recording", "video"),
   Show in folder and Delete work as for snips (Delete asks first if trimmed copies or GIFs saved
   beside it would go too). Its picture is made one video at a time, apart from snips' pictures,
   and not for a OneDrive file that's online only (that would download it); if the recorder
   process can't write `recording.json`, SnipForge does. It's never read for text. A recording's folder has
   no `original.png`, so older SnipForge versions sharing the folder don't list it, and
   `Library.entries()` (what opens images) still gives snips only.
9. **Hardening and docs**. One-hour soak test (drift, memory), low disk space, a device unplugged
   mid-recording, UAC, lock and sleep, several monitors with mixed scaling, rotation, HDR; the
   `IT-REVIEW.md` and README sections. If needed: the Media Foundation hardware encoder.
   Done so far: disk space (no start below 500 MB free; a warning below 1 GB; below 200 MB the
   recording stops and is kept, since a full drive can't finish the file), Windows "N" editions
   (`support.py`: the recorder and the self-test say to add the Media Feature Pack), a sound device
   unplugged (a warning, step 5), the screen lost (kept, step 5), sleep, shutdown and sign-out
   (the file is finished first, step 5b). Staying awake: while recording (or starting, or
   finishing; not while paused), Windows isn't to sleep or turn the screen off for inactivity
   (`power.stay_awake`, `SetThreadExecutionState` on the recorder process's main thread, following
   the recorder's state). Lock: Windows locked (Win+L, or by policy) pauses the recording with a
   note, also when it was locked in the countdown, before recording began; it's resumed from the
   control bar (`power.py` listens with `WTSRegisterSessionNotification`). The screen turning off anyway (a closed lid set to do
   nothing, say) is mentioned: the video shows the last picture until it's back
   (`RegisterPowerSettingNotification`, `GUID_CONSOLE_DISPLAY_STATE`).

Later, not in Phase 4: following a moving or covered window (Windows.Graphics.Capture), editing the
pointer effects after recording (by saving the pointer's path separately), noise suppression.

## Decisions

Approved by the maintainer on 2026-10-17, as recommended:

1. **Recordings live in the Library** with snips (one search, tags and favourites for everything).
2. **At most 1080p and 30 fps by default**, with "Original size" and 60 fps as options.
3. **A start-recording shortcut registered the normal way** (`RegisterHotKey`, e.g. Ctrl+Shift+R),
   not by widening the keyboard hook (Windows 11 uses Win+Shift+R for Snipping Tool's recorder,
   and `CLAUDE.md` limits the hook to Win+Shift+S and Print Screen); Record on the capture toolbar.
4. **Pillow writes GIFs** (+0.9 MB).
5. **The recorder runs as its own process** (`SnipForge.exe --record`).

Testing on a Mac: the macOS build workflow makes the `.dmg`; a coworker with a Mac installs it and
reports back, using the test plan in `docs/MAC-PLAN.md`. The webcam and choosing an area with the
mouse are tried by hand on Windows.
