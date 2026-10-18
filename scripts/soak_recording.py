"""Soak test for screen recording: one long recording through the real recorder and Qt's encoder,
checked for sound drifting from the picture and for memory growing (VIDEO-PLAN step 9).

    python scripts/soak_recording.py --minutes 60
    python scripts/soak_recording.py --analyze soak-results/soak.mp4 --duration 3600

By default nothing real is recorded: the screen is a moving band that flashes a white box every
MARK_EVERY seconds of recording, and the sound comes from two made-up sound cards whose clocks run
--drift fast and slow (0.1%: real ones are off by up to about that, 3.6 s an hour). Each stamps its
packets with when they were captured, as Windows does, and they take turns beeping on the flashes.
It passes if:
  - every beep is within TOLERANCE of its flash (uncorrected, they'd wander by seconds an hour);
  - the mixer kept each card to its timestamps by adding or dropping the samples the drift calls
    for, never by jumping (resyncs) or distrusting a stamp;
  - memory grew no more than MAX_GROWTH_MB from the first sample after warm-up to the last;
  - the file is whole, and its picture and sound both start at 0.
What it can't tell: how real devices' clocks behave (both the flashes and the beeps follow the
recorder's clock by design), stamps Windows gets wrong, and pauses.

--real-screen records the primary monitor and --real-sound the default microphone and speakers:
that checks memory and the counters on real devices, but there's nothing known to time. Ask before
using them on someone's PC: they record what's on it, and what's said near it.

Writes soak.mp4, samples.jsonl (every SAMPLE_EVERY seconds: memory, CPU and the recorder's
counters) and report.json to --out, and runs below normal priority, so the PC can be used meanwhile.
Exits 0 if it passed, 1 if not, 2 if it couldn't run.
"""
from __future__ import annotations

import argparse
import array
import bisect
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
DRIFT = 0.001  # how far off the made-up sound cards' clocks are: the system one fast, the microphone slow
# Seconds a beep may be from its flash. Marks fall on the 30 fps frame grid, so a flash shows on its
# mark's own frame; onsets are found in 5 ms steps; the mixer keeps sound within 2 ms.
TOLERANCE = 0.05
BEEP_SEARCH = 2.4  # seconds either side of a mark a beep is looked for (the other card's are 5 s away)
ADJUSTMENT_BAND = 0.5  # the mixer's corrections must come within this share of what the drift calls for
SAMPLE_EVERY = 30  # seconds between samples
MAX_GROWTH_MB = 100  # more than this from the first sample after warm-up to the last: something leaks
DEFAULT_OUT = Path("soak-results")


def drift_of(kind: str, drift: float) -> float:
    return drift if kind == "system" else -drift


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
    while (mark := FIRST_MARK + k * MARK_EVERY) < last:
        start = max(0, math.ceil((mark - first) / step))
        if beeper(k) == kind and mark + BEEP > first and start < count:
            data = data or array.array("f", bytes(8 * count))
            for i in range(start, count):
                into = first + i * step - mark
                if into >= BEEP:
                    break
                data[2 * i] = data[2 * i + 1] = LOUD * math.sin(2 * math.pi * BEEP_HZ * into)
        k += 1
    return data


# --- the made-up screen and sound cards -----------------------------------------------------------

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


# --- the machine ----------------------------------------------------------------------------------

def memory_mb() -> float | None:
    """This process's committed private memory (Task Manager's "Commit size"; Windows), or its peak
    resident size elsewhere, which never falls."""
    if sys.platform != "win32":
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / (2**20 if sys.platform == "darwin" else 2**10)
    from ctypes import wintypes

    class Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS_EX
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
    if sys.platform != "win32":
        os.nice(5)
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    if not kernel32.SetPriorityClass(ctypes.c_void_p(kernel32.GetCurrentProcess()), 0x4000):  # BELOW_NORMAL
        print(f"warning: couldn't lower this process's priority (error {ctypes.get_last_error()})", flush=True)


# --- reading the recording back -------------------------------------------------------------------

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


def beep_onsets(path, marks: list[float]) -> tuple[dict[float, float | None], str | None]:
    """When each beep starts in the sound (in 5 ms steps, decoded with Qt), looking up to BEEP_SEARCH
    either side of its mark, and the decoder's error, if it had one."""
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

    found: dict[float, float | None] = dict.fromkeys(marks)
    decoder, fmt, loop, problem = QAudioDecoder(), QAudioFormat(), QEventLoop(), []
    fmt.setSampleRate(RATE)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    decoder.setAudioFormat(fmt)

    def ready() -> None:
        buffer = decoder.read()
        start = buffer.startTime() / 1e6
        end = start + buffer.frameCount() / RATE
        near = [m for m in marks[bisect.bisect_left(marks, start - BEEP_SEARCH):
                                 bisect.bisect_right(marks, end + BEEP_SEARCH)] if found[m] is None]
        if not near:
            return
        samples = array.array("f")
        samples.frombytes(bytes(buffer.constData())[: buffer.byteCount()])
        for i in range(0, len(samples), 240):
            window = samples[i:i + 240]
            if math.sqrt(sum(v * v for v in window) / len(window)) > LOUD / 4:
                t = start + i / RATE
                for m in near:
                    if found[m] is None and abs(t - m) < BEEP_SEARCH:
                        found[m] = t

    def failed(*_args) -> None:
        problem.append(decoder.errorString() or "the sound couldn't be decoded")
        loop.quit()

    decoder.bufferReady.connect(ready)
    decoder.finished.connect(loop.quit)
    decoder.error.connect(failed)
    deadline = QTimer(singleShot=True, interval=30 * 60_000)
    deadline.timeout.connect(loop.quit)
    deadline.start()
    decoder.setSource(QUrl.fromLocalFile(str(path)))
    decoder.start()
    loop.exec()
    deadline.stop()
    decoder.stop()
    return found, (problem[0] if problem else None)


def timing(path, duration: float) -> dict:
    """Each flash and its beep, and whether every beep was found within TOLERANCE of its flash."""
    marks = mark_times(duration)
    flashes = flash_onsets(path, marks)
    beeps, problem = beep_onsets(path, marks)
    rows = [{"mark": m, "source": beeper(n), "flash": flashes[m], "beep": beeps[m],
             "beep_minus_flash": None if flashes[m] is None or beeps[m] is None else round(beeps[m] - flashes[m], 4)}
            for n, m in enumerate(marks)]
    offsets = [r["beep_minus_flash"] for r in rows]
    found = [abs(o) for o in offsets if o is not None]
    missing = len(offsets) - len(found)
    return {"ok": bool(rows) and missing == 0 and max(found) <= TOLERANCE, "marks": len(rows),
            "missing": missing, "worst": max(found, default=None), "decoder_error": problem, "rows": rows}


def sound_verdict(sound: dict, drift: float) -> dict:
    """Whether the mixer kept each made-up card to its timestamps the gentle way: adding (or
    dropping) the samples its drift calls for, one at a time, and never jumping or distrusting."""
    sources = {}
    for kind in ("system", "microphone"):
        stats = sound.get(kind, {})
        expected = -drift_of(kind, drift) * RATE * stats.get("placed_s", 0.0)
        adjusted = stats.get("adjustments", 0)
        sources[kind] = {
            "ok": bool(stats) and all(stats.get(k, 0) == 0 for k in ("resyncs", "bad_stamps", "late_frames"))
            and abs(adjusted - expected) <= ADJUSTMENT_BAND * abs(expected),
            "adjustments": adjusted, "expected": round(expected),
            **{k: stats.get(k) for k in ("resyncs", "bad_stamps", "late_frames", "placed_s")}}
    return {"ok": all(s["ok"] for s in sources.values()), "sources": sources}


def container_verdict(path) -> dict:
    """The file's picture and sound both start at 0, as the recorder writes them."""
    from snipforge.video import ffmpeg

    info = ffmpeg.probe(path)
    starts = {kind: (info.first(kind).start if info.first(kind) else None) for kind in ("video", "audio")}
    ok = all(start is not None and abs(start) < 0.05 for start in starts.values())
    return {"ok": ok, "starts": starts, "duration": info.duration}


def memory_verdict(samples: list[dict], seconds: float) -> dict:
    """Growth from the first sample after warm-up (a tenth of the run, at least one sample in) to
    the last, and the fitted slope. A long run without readings fails: it can't be told."""
    points = [(s["wall_s"], s["memory_mb"]) for s in samples if s.get("memory_mb") is not None]
    if len(points) < 3:
        return {"ok": seconds < 120, "growth_mb": None, "note": "too few memory readings to tell"}
    settled = points[max(1, len(points) // 10):]
    first, last = settled[0][1], settled[-1][1]
    mean_t = sum(t for t, _ in settled) / len(settled)
    mean_m = sum(m for _, m in settled) / len(settled)
    spread = sum((t - mean_t) ** 2 for t, _ in settled)
    slope = sum((t - mean_t) * (m - mean_m) for t, m in settled) / spread * 60 if spread else 0.0
    growth = round(last - first, 1)
    return {"ok": growth <= MAX_GROWTH_MB, "growth_mb": growth, "slope_mb_per_min": round(slope, 3),
            "first_mb": round(first, 1), "last_mb": round(last, 1), "peak_mb": round(max(m for _, m in points), 1)}


# --- the run --------------------------------------------------------------------------------------

def parse(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--minutes", type=float, default=60.0)
    parser.add_argument("--seconds", type=float, help="instead of --minutes, for short runs")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--size", default="1920x1080", help="the made-up screen's size")
    parser.add_argument("--drift", type=float, default=DRIFT, help="the made-up sound cards' clock error")
    parser.add_argument("--real-screen", action="store_true", help="record the primary monitor (ask first)")
    parser.add_argument("--real-sound", action="store_true", help="record the microphone and speakers (ask first)")
    parser.add_argument("--normal-priority", action="store_true")
    parser.add_argument("--analyze", type=Path, help="only check the flashes and beeps of a soak.mp4")
    parser.add_argument("--duration", type=float, help="with --analyze: how long the recording is")
    return parser.parse_args(argv)


def screen_area(args) -> tuple[int, int, int, int]:
    if args.real_screen:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        g, ratio = screen.geometry(), screen.devicePixelRatio()
        return round(g.x() * ratio), round(g.y() * ratio), round(g.width() * ratio), round(g.height() * ratio)
    try:
        width, height = (int(v) for v in args.size.lower().split("x"))
    except ValueError:
        raise ValueError(f"--size must be like 1920x1080, not {args.size!r}") from None
    return 0, 0, width, height


def make_recorder(args, area, output: Path):
    """The real Recorder, with the made-up screen and sound cards unless real ones were asked for.
    At the product's own size cap (1080p)."""
    from snipforge.video.recorder import Recorder, Settings
    from snipforge.video.screen_frames import ScreenFrames

    held, extras = {}, {}
    if not args.real_screen:
        extras["screen_frames"] = lambda a, fps, clock, _overlay: ScreenFrames(
            a, fps, clock, Flasher(clock, a), open_screen=PatternScreen, first_image=_grey)
        extras["overlay"] = lambda s: None
    if not args.real_sound:
        extras["capture"] = lambda kind, _device, on_packet, _on_error: DriftingSound(
            kind, on_packet, lambda: held["recorder"].clock, drift_of(kind, args.drift))
    held["recorder"] = Recorder(Settings(area, output), **extras)
    return held["recorder"]


def record(recorder, seconds: float, out: Path) -> tuple[dict, list[dict], float]:
    """Record for `seconds`, sampling every SAMPLE_EVERY (the last just before it stops): the outcome
    (result, or failure), the samples, and when it began (perf_counter)."""
    from PySide6.QtCore import QEventLoop, QTimer

    samples, outcome, loop = [], {}, QEventLoop()
    began = time.perf_counter()
    recorder.finished.connect(lambda result: (outcome.update(result=result), loop.quit()))
    recorder.failed.connect(lambda message: (outcome.update(failure=message), loop.quit()))
    recorder.cancelled.connect(lambda: (outcome.update(failure="cancelled: too little was recorded"), loop.quit()))
    recorder.warning.connect(lambda message: print("warning:", message, flush=True))
    try:
        recorder.start()
    except Exception as exc:  # RecordingError: too little disk space, no encoder
        return {"failure": f"couldn't start: {exc}"}, samples, began
    with (out / "samples.jsonl").open("w", encoding="utf-8") as log:
        def sample() -> None:
            row = _sample(recorder, began)
            samples.append(row)
            log.write(json.dumps(row) + "\n")
            log.flush()
            print(f"{row['wall_s']:>7.0f} s  memory {row['memory_mb'] or 0:7.1f} MB  frames {row['frames_made']}"
                  f"  late-skipped {row['frames_skipped_late']}  dropped {row['frames_dropped_encoder']}", flush=True)

        ticker = QTimer(interval=int(min(SAMPLE_EVERY, max(1.0, seconds / 4)) * 1000))
        ticker.timeout.connect(sample)
        ender = QTimer(singleShot=True, interval=int(seconds * 1000))
        ender.timeout.connect(lambda: (ticker.stop(), sample(), recorder.stop()))  # the last, still recording
        ticker.start()
        ender.start()
        loop.exec()
        ticker.stop()
        ender.stop()
    return outcome, samples, began


def _sample(recorder, began: float) -> dict:
    stats = recorder.stats()
    return {"wall_s": round(time.perf_counter() - began, 1), "media_s": round(recorder.media_time() or 0, 2),
            "memory_mb": memory_mb(), "cpu_s": round(time.process_time(), 1),
            **{k: v for k, v in stats.items() if k != "sound"}, "sound": stats.get("sound")}


def build_report(args, area, output: Path, seconds: float, outcome: dict, samples: list, began: float) -> dict:
    synthetic = not (args.real_screen or args.real_sound)
    report: dict = {"seconds_asked": seconds, "area": area, "synthetic": synthetic, "drift": args.drift}
    if "result" not in outcome:
        report.update(ok=False, failure=outcome.get("failure", "never finished"))
        return report
    result = outcome["result"]
    report.update(duration=result.duration, stats=result.stats, memory=memory_verdict(samples, seconds),
                  memory_after_finish_mb=memory_mb(),
                  cpu_share=round(time.process_time() / max(1e-9, time.perf_counter() - began), 2))
    _write(report, args.out)  # what's known so far, should reading it back go wrong
    checks = [report["memory"]["ok"]]
    try:
        report["container"] = container_verdict(output)
        checks.append(report["container"]["ok"])
        if synthetic:
            report["sound"] = sound_verdict(result.stats.get("sound", {}), args.drift)
            report["timing"] = timing(output, result.duration)
            checks += [report["sound"]["ok"], report["timing"]["ok"]]
    except Exception as exc:  # FFmpegError reading it back: say so, keep the rest
        report["analysis_error"] = repr(exc)
        checks.append(False)
    report["ok"] = all(checks)
    return report


def analysis_report(path: Path, duration: float | None) -> dict:
    """--analyze: the flashes and beeps of a soak.mp4 made earlier, and how its streams start."""
    from snipforge.video import ffmpeg

    duration = duration if duration is not None else (ffmpeg.probe(path).duration or 0.0)
    report = {"analyzed": str(path), "duration": duration, "container": container_verdict(path),
              "timing": timing(path, duration)}
    report["ok"] = report["container"]["ok"] and report["timing"]["ok"]
    return report


def _write(report: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def _finish(report: dict, out: Path) -> int:
    _write(report, out)
    summary = {k: report.get(k) for k in ("ok", "duration", "memory", "cpu_share", "failure", "analysis_error")}
    print(json.dumps({k: v for k, v in summary.items() if v is not None}, default=str))
    for part in ("sound", "timing", "container"):
        if part in report:
            print(part, json.dumps({k: v for k, v in report[part].items() if k != "rows"}, default=str))
    return 0 if report.get("ok") else 1


def main(argv=None) -> int:
    args = parse(argv)
    if not args.real_screen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])  # kept for the run
    if args.analyze:
        return _finish(analysis_report(args.analyze, args.duration), args.out)
    try:
        area = screen_area(args)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not args.normal_priority:
        lower_priority()
    seconds = args.seconds if args.seconds is not None else args.minutes * 60
    args.out.mkdir(parents=True, exist_ok=True)
    output = (args.out / "soak.mp4").resolve()
    recorder = make_recorder(args, area, output)
    outcome, samples, began = record(recorder, seconds, args.out)
    return _finish(build_report(args, area, output, seconds, outcome, samples, began), args.out)


def _grey(area):
    from PySide6.QtGui import QColor, QImage

    image = QImage(area[2], area[3], QImage.Format.Format_RGB32)
    image.fill(QColor(40, 40, 48))
    return image


if __name__ == "__main__":
    sys.exit(main())
