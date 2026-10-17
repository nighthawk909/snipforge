# Video recording prototype (throwaway)

Proof that the stack in [docs/VIDEO-PLAN.md](../../docs/VIDEO-PLAN.md) works on a real Windows PC.
Not part of SnipForge: not packaged, not tested by CI, no error handling to speak of. The real
feature will be rebuilt properly, step by step, as the plan describes.

| File | What it shows |
|---|---|
| `record_prototype.py` | The whole recorder: area capture, pointer effects, mic + system audio, control bar, pause, MKV → MP4, and a check of the result |
| `dxgi_capture.py` | Screen frames of just the area, via DXGI Desktop Duplication |
| `wasapi_capture.py` | Microphone and system audio (loopback), via WASAPI |
| `mixer.py` | Puts both audio sources on one timeline by their timestamps |
| `cursor.py` | Pointer image, highlight and click rings (the real feature reuses `snipforge/pointer.py`) |
| `com.py` | The bit of COM-through-ctypes the two capture files need |
| `trim_spike.py` | Trim (and MKV → MP4) without re-encoding, with the FFmpeg libraries Qt ships |
| `gif_spike.py` | MP4 → GIF: frames decoded with Qt's FFmpeg, written with Pillow |

## Running it

From the repository root, in the dev environment (it needs `PySide6-Addons` for QtMultimedia,
which `pip install PySide6` brings; Pillow for the GIF spike is in `requirements-dev.txt`):

```bash
python prototypes/video/record_prototype.py
```

That records 10 s of a 1280×720 area in the middle of the main monitor, with the microphone and
system audio, to `Videos\snipforge-prototype.mp4` (`--out` to change), then plays the file back with Qt and prints what it
found. Talk or play some audio while it runs to hear both sources. Other options:

| Option | |
|---|---|
| `--choose` | Pick the area with SnipForge's own capture overlay (drag, click a window, or Enter) |
| `--area X,Y,W,H` | An area in physical desktop pixels |
| `--seconds 10 --fps 30` | Length and frame rate |
| `--max-width 1920` | Scale the video down (the plan's default cap) |
| `--no-mic`, `--no-system`, `--no-effects` | Leave out a source, or the highlight and click rings |
| `--webcam` | Picture-in-picture from the default camera (**switches the camera on**) |
| `--pause-at 3 --resume-at 4.5` | Pause and resume during the recording |
| `--selftest` | Shows a test pattern in the area that flashes and beeps at 2, 5 and 8 s, puts the control bar (in magenta) inside the area, then measures how far apart sound and picture are in the file and checks the bar never appears. **Plays three short beeps.** |
| `--demo-pointer` | With `--selftest`: circles the mouse pointer over the pattern and clicks it twice (**moves your mouse** for the length of the recording), to show the highlight and click rings |
| `--debug-wav` | Also writes the mixer's own output as a WAV next to the MP4 |

Environment variables for digging: `KEEP_MKV=1` keeps the raw recording, `DEBUG_STALLS=1` prints
every thread's stack whenever the main thread stops for more than 0.4 s.

```bash
python prototypes/video/trim_spike.py in.mp4 out.mp4 4.5 8.5
python prototypes/video/gif_spike.py in.mp4 out.gif --fps 12 --width 960
```

## What a self-test run prints (on the maintainer's PC, 2026-10-17)

```
Recording 10 s of 1280x720 at (1080,360), 30 fps -> prototype-recording.mp4

Wrote ...\prototype-recording.mp4 (3.7 MB; recorded as MKV, copied into MP4 in 23 ms)
Video: 296 frames made, 296 sent to the encoder, 4 dropped; 437 screen updates; duplication reopened 0x
Drops: 4 capture thread late, 0 queue full, 0 encoder behind; longest main-thread gap 213 ms, slowest frame 8 ms
Audio mic: 1047 packets, 10.02 s placed, peak -75.6 dBFS, resyncs 1
Audio system: 1056 packets, 10.07 s placed, peak -10.1 dBFS, resyncs 1
Audio devices opened in 0.76 s
Control bar excluded from capture: True
Clicks seen by the pointer overlay: 0
CPU: 14.2 s over 10.2 s = 9% of 16 logical cores (139% of one)
First video frame index sent: 0

File check (decoded with Qt): (1280, 720) video, 296 frames, duration 10.01 s, audio track True, audio 10.01 s, peak -10.2 dBFS
Frames showing the magenta control bar: 0
Flashes seen at  [2.033, 5.033, 8.033]
Beeps heard at   [2.117, 5.12, 8.117]
Audio minus video (ms): [84, 87, 84]  (positive = sound later; includes the speakers' own latency)
Started at       [2.0, 4.997, 7.997]
Picture path (ms): [33, 36, 36]  sound path (ms): [117, 123, 120]  (from starting the flash + beep to the file)
```

The beep reaches the file ~0.1 s after the flash because that's how long Windows takes to play it:
both are measured from the moment the prototype started them, and the recorder adds no offset of
its own. The microphone level is low because the room was quiet.

Not tried on the test PC: `--webcam` (it would have switched on the maintainer's camera unasked) and
`--choose` with a mouse (it was checked by pressing Enter on the overlay from a script).
