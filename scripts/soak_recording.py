"""Soak test for screen recording: one long recording through the real recorder and Qt's encoder,
checked for sound drifting from the picture and for memory growing (VIDEO-PLAN step 9).

    python scripts/soak_recording.py --minutes 60 --out soak

By default nothing real is recorded: the screen is a moving pattern that flashes a white box every
MARK_EVERY seconds of recording, and the sound comes from two made-up sound cards whose clocks run
0.1% fast and 0.1% slow (real ones are off by up to ~0.1%: up to 3.6 s an hour). Each stamps its
packets with when they were captured, as Windows does, and beeps at its own flashes (the two take
turns). If the mixer didn't keep each source to its timestamps, the beeps would wander from the
flashes by seconds over the hour; they must stay within TOLERANCE. It runs below normal priority,
so the PC can be used meanwhile.

--real-screen records the primary monitor and --real-sound the default microphone and speakers:
that checks memory and the counters on real devices, but there's no known flash or beep to time.
Ask before using them on someone's PC: they record what's on it, and what's said near it.

Writes soak.mp4, samples.jsonl (every SAMPLE_EVERY seconds: memory and the recorder's counters)
and report.json to --out. Exits 0 if the recording is whole, in time and memory stayed level.
"""
from __future__ import annotations

import argparse
import array
import ctypes
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RATE, PACKET = 48000, 480  # the made-up sound cards: 10 ms packets at "48 kHz"
FIRST_MARK, MARK_EVERY = 2.0, 5.0  # seconds of recording: the first flash, then one every so often
FLASH, BEEP, BEEP_HZ, LOUD = 0.2, 0.1, 1000.0, 0.5
DRIFT = {"system": 0.001, "microphone": -0.001}  # how fast each made-up sound card's clock runs
TOLERANCE = 0.05  # seconds a beep may be from its flash (a flash shows on the next frame: up to 33 ms)
SAMPLE_EVERY = 30  # seconds between samples
MAX_GROWTH_MB = 100  # more than this from the first sample after warm-up to the last: memory leaks


def mark_times(duration: float) -> list[float]:
    """Seconds into the recording of every flash in a recording `duration` long, with time after it
    to see it."""
    count = max(0, math.floor((duration - FIRST_MARK - 1.0) / MARK_EVERY) + 1)
    return [FIRST_MARK + k * MARK_EVERY for k in range(count)]


def beeper(n: int) -> str:
    """Which made-up sound card beeps at the n-th flash: they take turns."""
    return "system" if n % 2 == 0 else "microphone"


def flashing(media: float) -> bool:
    if media < FIRST_MARK:
        return False
    return (media - FIRST_MARK) % MARK_EVERY < FLASH


def beep_samples(first: float, count: int, step: float, kind: str) -> array.array | None:
    """Stereo float samples for `count` frames whose first is `first` seconds into the recording and
    each `step` later: the beep where it falls on one of `kind`'s marks, silence elsewhere. None
    when it's all silence."""
    data = None
    last = first + count * step
    k = max(0, math.floor((first - BEEP - FIRST_MARK) / MARK_EVERY))
    while True:
        mark = FIRST_MARK + k * MARK_EVERY
        if mark > last:
            return data
        if beeper(k) == kind and mark + BEEP > first:
            data = data or array.array("f", bytes(8 * count))
            for i in range(max(0, math.ceil((mark - first) / step)), count):
                into = first + i * step - mark
                if into >= BEEP:
                    break
                data[2 * i] = data[2 * i + 1] = LOUD * math.sin(2 * math.pi * BEEP_HZ * into)
        k += 1


class PatternScreen:
    """Stands in for the screen: a band that moves ten times a second, so the encoder has work."""

    def __init__(self, area) -> None:
        self.width, self.height, self.moves = area[2], area[3], 0

    def grab(self, timeout_ms: int):
        from PySide6.QtGui import QColor, QImage, QPainter

        time.sleep(timeout_ms / 1000)
        self.moves += 1
        image = QImage(self.width, self.height, QImage.Format.Format_RGB32)
        image.fill(QColor(40, 40, 48))
        painter = QPainter(image)
        x = self.moves * 16 % self.width
        painter.fillRect(x, 0, max(8, self.width // 12), self.height, QColor.fromHsv(self.moves * 7 % 360, 160, 200))
        painter.end()
        return bytes(image.constBits()), image.bytesPerLine()

    def close(self) -> None:
        pass


class Flasher:
    """Draws the white box into each frame while a flash is due (as the pointer overlay would draw)."""

    def __init__(self, clock, area) -> None:
        self.clock, self.width, self.height = clock, area[2], area[3]

    def draw(self, frame, now: float) -> None:
        from PySide6.QtGui import QColor, QPainter

        media = self.clock.media_time(now)
        if media is not None and flashing(media):
            painter = QPainter(frame)
            painter.fillRect(self.width // 2 - 40, self.height // 2 - 40, 80, 80, QColor("white"))
            painter.end()


class DriftingSound:
    """A made-up sound card whose clock runs `drift` fast (0.001: 0.1%): it calls 48,000 samples a
    second what are really 48,000 × (1 + drift). Packets are stamped with when they were captured."""

    def __init__(self, kind: str, on_packet, clock_of, drift: float) -> None:
        self.kind, self.on_packet, self.clock_of = kind, on_packet, clock_of
        self.rate = RATE * (1 + drift)  # samples a real second
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name=f"soak-{self.kind}", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        started, sent, silence = time.perf_counter(), 0, bytes(8 * PACKET)
        while not self.stopping.is_set():
            captured = started + sent * PACKET / self.rate
            if time.perf_counter() < captured + PACKET / self.rate:
                time.sleep(0.002)
                continue
            media = self.clock_of().media_time(captured)
            data = beep_samples(media, PACKET, 1 / self.rate, self.kind) if media is not None else None
            self.on_packet(data.tobytes() if data is not None else silence, PACKET, captured, False)
            sent += 1

    def stop(self) -> None:
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(2)


def memory_mb() -> float | None:
    """This process's private memory (Windows), or its peak resident size elsewhere."""
    if sys.platform != "win32":
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / (2**20 if sys.platform == "darwin" else 2**10)
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                "PrivateUsage")]

    counters = Counters(cb=ctypes.sizeof(Counters))
    kernel32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return counters.PrivateUsage / 2**20


def lower_priority() -> None:
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass(ctypes.c_void_p(kernel32.GetCurrentProcess()), 0x4000)  # BELOW_NORMAL
    else:
        os.nice(5)


def flash_onsets(path, marks: list[float]) -> dict[float, float | None]:
    """When each flash shows first in the video, looking near its mark."""
    from snipforge.video import frames

    found = {}
    for mark in marks:
        found[mark] = None
        for t, image in frames.read(path, width=160, start=max(0.0, mark - 0.3), end=mark + 0.6):
            if image.pixelColor(image.width() // 2, image.height() // 2).lightness() > 200:
                found[mark] = t
                break
    return found


def beep_onsets(path, marks: list[float]) -> dict[float, float | None]:
    """When each beep starts in the sound, in 5 ms steps, looking near its mark (decoded with Qt)."""
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

    found: dict[float, float | None] = dict.fromkeys(marks)
    decoder, fmt = QAudioDecoder(), QAudioFormat()
    fmt.setSampleRate(RATE)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    decoder.setAudioFormat(fmt)
    loop = QEventLoop()

    def ready() -> None:
        buffer = decoder.read()
        start = buffer.startTime() / 1e6
        end = start + buffer.frameCount() / RATE
        near = [m for m in marks if found[m] is None and m - 0.3 < end and start < m + 0.6]
        if not near:
            return
        samples = array.array("f")
        samples.frombytes(bytes(buffer.constData())[: buffer.byteCount()])
        for i in range(0, len(samples) - 240, 240):
            t = start + i / RATE
            if math.sqrt(sum(v * v for v in samples[i:i + 240]) / 240) > LOUD / 4:
                for m in near:
                    if found[m] is None and m - 0.3 < t < m + 0.6:
                        found[m] = t

    decoder.bufferReady.connect(ready)
    decoder.finished.connect(loop.quit)
    decoder.error.connect(lambda *_: loop.quit())
    QTimer.singleShot(30 * 60_000, loop.quit)
    decoder.setSource(QUrl.fromLocalFile(str(path)))
    decoder.start()
    loop.exec()
    return found


def timing(path, duration: float) -> dict:
    """Each flash and its beep, and whether every beep was within TOLERANCE of its flash."""
    marks = mark_times(duration)
    flashes, beeps = flash_onsets(path, marks), beep_onsets(path, marks)
    rows = [{"mark": m, "source": beeper(n), "flash": flashes[m], "beep": beeps[m],
             "beep_minus_flash": None if flashes[m] is None or beeps[m] is None else round(beeps[m] - flashes[m], 4)}
            for n, m in enumerate(marks)]
    offsets = [r["beep_minus_flash"] for r in rows]
    ok = bool(rows) and all(o is not None and abs(o) <= TOLERANCE for o in offsets)
    return {"ok": ok, "marks": len(rows), "worst": max((abs(o) for o in offsets if o is not None), default=None),
            "rows": rows}


def memory_verdict(samples: list[dict]) -> dict:
    """Growth from the first sample after warm-up (a tenth of the run, at least one sample in) to
    the last."""
    values = [s["memory_mb"] for s in samples if s.get("memory_mb") is not None]
    if len(values) < 3:
        return {"ok": True, "growth_mb": None, "note": "too short to tell"}
    first = values[max(1, len(values) // 10)]
    growth = round(values[-1] - first, 1)
    return {"ok": growth <= MAX_GROWTH_MB, "growth_mb": growth, "first_mb": round(first, 1),
            "last_mb": round(values[-1], 1), "peak_mb": round(max(values), 1)}


def parse(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--minutes", type=float, default=60.0)
    parser.add_argument("--seconds", type=float, help="instead of --minutes, for short runs")
    parser.add_argument("--out", type=Path, default=Path("soak"))
    parser.add_argument("--size", default="1920x1080", help="the made-up screen's size")
    parser.add_argument("--real-screen", action="store_true", help="record the primary monitor (ask first)")
    parser.add_argument("--real-sound", action="store_true", help="record the microphone and speakers (ask first)")
    parser.add_argument("--normal-priority", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse(argv)
    synthetic = not (args.real_screen or args.real_sound)
    if not args.real_screen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from snipforge.video.recorder import Recorder, Settings
    from snipforge.video.screen_frames import ScreenFrames

    _app = QApplication.instance() or QApplication([])  # kept for the run
    if not args.normal_priority:
        lower_priority()
    seconds = args.seconds if args.seconds is not None else args.minutes * 60
    args.out.mkdir(parents=True, exist_ok=True)
    output = (args.out / "soak.mp4").resolve()
    if args.real_screen:
        screen = QGuiApplication.primaryScreen()
        g, ratio = screen.geometry(), screen.devicePixelRatio()
        area = (round(g.x() * ratio), round(g.y() * ratio), round(g.width() * ratio), round(g.height() * ratio))
    else:
        width, height = (int(v) for v in args.size.lower().split("x"))
        area = (0, 0, width, height)
    held = {}
    extras = {}
    if not args.real_screen:
        extras["screen_frames"] = lambda a, fps, clock, _overlay: ScreenFrames(
            a, fps, clock, Flasher(clock, a), open_screen=PatternScreen, first_image=lambda a: _grey(a))
        extras["overlay"] = lambda s: None
    if not args.real_sound:
        extras["capture"] = lambda kind, _device, on_packet, _on_error: DriftingSound(
            kind, on_packet, lambda: held["recorder"].clock, DRIFT[kind])
    recorder = held["recorder"] = Recorder(Settings(area, output, max_size=None), **extras)

    samples, outcome, loop = [], {}, QEventLoop()
    began = time.perf_counter()
    log = (args.out / "samples.jsonl").open("w", encoding="utf-8")

    def sample() -> None:
        stats = recorder.stats()
        row = {"wall_s": round(time.perf_counter() - began, 1), "media_s": round(recorder.media_time() or 0, 2),
               "memory_mb": memory_mb(), "cpu_s": round(time.process_time(), 1),
               **{k: v for k, v in stats.items() if k != "sound"},
               "sound": stats.get("sound")}
        samples.append(row)
        log.write(json.dumps(row) + "\n")
        log.flush()
        print(f"{row['wall_s']:>7.0f} s  memory {row['memory_mb'] or 0:7.1f} MB  frames {row['frames_made']}"
              f"  late-skipped {row['frames_skipped_late']}  dropped {row['frames_dropped_encoder']}", flush=True)

    recorder.finished.connect(lambda result: (outcome.update(result=result), loop.quit()))
    recorder.failed.connect(lambda message: (outcome.update(failure=message), loop.quit()))
    recorder.warning.connect(lambda message: print("warning:", message, flush=True))
    ticker = QTimer(interval=int(min(SAMPLE_EVERY, max(1.0, seconds / 4)) * 1000))
    ticker.timeout.connect(sample)
    recorder.start()
    ticker.start()
    last = lambda: (ticker.stop(), sample(), recorder.stop())  # noqa: E731 - the last sample, still recording
    QTimer.singleShot(int(seconds * 1000), last)
    loop.exec()
    ticker.stop()
    log.close()

    report: dict = {"seconds_asked": seconds, "area": area, "synthetic": synthetic}
    if "result" not in outcome:
        report.update(ok=False, failure=outcome.get("failure", "never finished"))
    else:
        result = outcome["result"]
        report.update(duration=result.duration, stats=result.stats, memory=memory_verdict(samples),
                      memory_after_finish_mb=memory_mb(), cpu_s=round(time.process_time(), 1),
                      cpu_share=round(time.process_time() / max(1e-9, time.perf_counter() - began), 2))
        if synthetic:
            report["timing"] = timing(output, result.duration)
        report["ok"] = report["memory"]["ok"] and report.get("timing", {"ok": True})["ok"]
    (args.out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in ("ok", "duration", "memory", "cpu_share", "failure")}, default=str))
    if "timing" in report:
        print(json.dumps({k: report["timing"][k] for k in ("ok", "marks", "worst")}))
    return 0 if report["ok"] else 1


def _grey(area):
    from PySide6.QtGui import QColor, QImage

    image = QImage(area[2], area[3], QImage.Format.Format_RGB32)
    image.fill(QColor(40, 40, 48))
    return image


if __name__ == "__main__":
    sys.exit(main())
