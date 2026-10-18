"""The recorder process: `SnipForge.exe --record <channel>`, started by SnipForge for each recording.

Recording in a process of its own means nothing else SnipForge does (an editor busy with a big
image, a stall on its main thread) can hold up a recording. It connects back to SnipForge over the
channel (channel.py), says hello, gets the recording's settings, records, reports how it's going,
and exits once the recording has ended. It shows nothing: a window drawing here (the control bar,
styled, once was) holds Python's lock while Qt lays it out, and the frame thread waits on that lock,
so a slow machine starved the recording of frames. The control bar is SnipForge's, fed with progress
(time and levels) four times a second. Windows going to sleep or ending the session finishes the
file there and then (power.py), and so does SnipForge going away. Whatever makes this process quit,
a recording in progress is finished first, so the file is kept.

    SnipForge                              recorder process
      start ─► launches it ──────────────► hello {pid, window, version}
      settings ─────────── start ────────►  Recorder.start(hold)
      (countdown) ───────── go ──────────►  Recorder.go()
                ◄───────── state, warning ─
      pause / resume / stop / cancel ────►
                ◄───────── progress {time, levels} ─ (4 a second while recording)
                ◄──────── finished {path, duration, stats, ended_by} | failed | cancelled
                                            exits
"""
from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import QCoreApplication, QObject, QTimer

from .. import __version__
from .channel import Channel, connect, is_name, settings_from_message
from .power import SessionWatch
from .recorder import ACTIVE, Recorder, RecordingError

log = logging.getLogger(__name__)

RECORD_FLAG = "--record"
CONNECT_TIMEOUT_MS = 10_000
START_TIMEOUT_MS = 15_000  # from hello to being told what to record
SLEEP_FINISH_SECONDS = 1.5  # Windows allows about 2 s before it sleeps
END_FINISH_SECONDS = 4.0  # and about 5 s before it offers to end an app that holds up shutdown
EXIT_FINISH_SECONDS = 10.0  # quitting for any other reason: finish the file first
COMMANDS = ("go", "pause", "resume", "stop", "cancel")
PROGRESS_MS = 250


def channel_name(argv: list[str]) -> str | None:
    """The channel name after --record, if it's one SnipForge makes."""
    try:
        name = argv[argv.index(RECORD_FLAG) + 1]
    except (ValueError, IndexError):
        return None
    return name if is_name(name) else None


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    argv = sys.argv if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    name = channel_name(argv)
    if name is None:
        log.error("SnipForge's recorder needs the channel to SnipForge: --record <name>")
        return 2
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)
    try:
        socket = connect(name, CONNECT_TIMEOUT_MS)
    except OSError as exc:
        log.error("%s", exc)
        return 3
    watch = SessionWatch()
    child = RecorderChild(Channel(socket), watch)
    sys.excepthook = child.unexpected  # an error in a slot ends the recording cleanly, not silently
    code = app.exec()
    watch.close()  # here, not while Windows is still telling its hidden window something
    return code


class RecorderChild(QObject):
    """The recorder process's side of the channel: runs one Recorder as SnipForge says."""

    def __init__(self, channel: Channel, watch, *, make_recorder=None, quit=None) -> None:
        super().__init__()
        self.channel, self.watch = channel, watch
        self.recorder = None
        self.ended_by: str | None = None  # "sleep", "session end", "SnipForge closed"
        self._make_recorder = make_recorder or Recorder
        self._progress = QTimer(self, interval=PROGRESS_MS)
        self._progress.timeout.connect(self._send_progress)
        self._quit = quit or QCoreApplication.exit
        self._done = False
        channel.message.connect(self._on_message)
        channel.closed.connect(self._parent_gone)
        watch.sleeping.connect(self._sleep)
        watch.ending_soon.connect(self._ending_soon)
        watch.ending.connect(self._ending)
        watch.not_ending.connect(watch.allow_shutdown)
        channel.send("hello", pid=os.getpid(), window=watch.window_id, version=__version__)
        QTimer.singleShot(START_TIMEOUT_MS, self, self._check_told)

    def unexpected(self, kind, error, trace) -> None:
        """sys.excepthook: log it, keep what was recorded, and end."""
        log.error("The recorder hit an unexpected problem", exc_info=(kind, error, trace))
        if not self._done:
            self.channel.send("warning", message=f"The recorder hit an unexpected problem: {error}")
            self._finish(1)

    # --- from SnipForge --------------------------------------------------------------------------

    def _on_message(self, message: dict) -> None:
        kind = message["type"]
        if self.recorder is None:
            if kind == "start":
                self._start(message.get("settings"), hold=message.get("hold") is True)
            elif kind == "ping":
                self.channel.send("pong", version=__version__)
                self._finish(0)
            elif kind in ("quit", "stop", "cancel"):  # called off before it began
                self._finish(0)
            else:
                log.warning("The recorder ignored %r before starting", kind)
        elif kind in COMMANDS:
            getattr(self.recorder, kind)()
        else:
            log.warning("The recorder ignored %r while recording", kind)

    def _start(self, data, hold: bool = False) -> None:
        try:
            settings = settings_from_message(data)
        except ValueError as exc:
            self._report_failure(str(exc))
            return
        self.recorder = recorder = self._make_recorder(settings)
        recorder.state_changed.connect(lambda state: self.channel.send("state", state=state))
        recorder.warning.connect(lambda text: self.channel.send("warning", message=text))
        recorder.finished.connect(self._finished)
        recorder.failed.connect(self._report_failure)
        recorder.cancelled.connect(self._cancelled)
        try:
            recorder.start(hold=hold)
        except RecordingError as exc:
            self._report_failure(str(exc))
            return
        self._progress.start()

    def _send_progress(self) -> None:
        """Time recorded and sound levels, for SnipForge's control bar."""
        if self.recorder is not None and self.recorder.state in ("recording", "paused"):
            levels = {kind: round(level, 3) for kind, level in self.recorder.levels().items()}
            self.channel.send("progress", time=round(self.recorder.media_time(), 3), levels=levels)

    def _check_told(self) -> None:
        if self.recorder is None and not self._done:
            log.error("SnipForge never said what to record")
            self._finish(2)

    def _parent_gone(self) -> None:
        if self._done:
            return
        if self.recorder is None:
            self._finish(2)
        elif self.recorder.state in ACTIVE:
            log.warning("SnipForge went away: finishing the recording")
            self.ended_by = "SnipForge closed"
            self.recorder.stop()  # then _finished (or _cancelled) ends this process

    # --- from Windows ----------------------------------------------------------------------------

    def _sleep(self) -> None:
        if self.recorder is not None and self.recorder.state in (*ACTIVE, "finishing"):
            self.ended_by = self.ended_by or "sleep"
            if not self.recorder.finish_now(SLEEP_FINISH_SECONDS):
                self._late("Windows went to sleep before the recording was finished; it finishes on waking")

    def _ending_soon(self) -> None:
        if self.recorder is not None and self.recorder.state in ACTIVE:
            self.watch.block_shutdown("Saving your screen recording")  # shown if we hold Windows up

    def _ending(self) -> None:
        if self.recorder is not None and self.recorder.state in (*ACTIVE, "finishing"):
            self.ended_by = self.ended_by or "session end"
            if not self.recorder.finish_now(END_FINISH_SECONDS):
                self._late("Windows ended the session before the recording was finished")
        self.watch.allow_shutdown()

    def _late(self, message: str) -> None:
        log.warning(message)
        self.channel.send("warning", message=message)

    # --- to SnipForge ----------------------------------------------------------------------------

    def _finished(self, result) -> None:
        try:
            self.channel.send("finished", path=str(result.path), duration=result.duration, stats=result.stats,
                              ended_by=self.ended_by)
        finally:
            self._finish(0)

    def _report_failure(self, message: str) -> None:
        try:
            self.channel.send("failed", message=message)
        finally:
            self._finish(1)

    def _cancelled(self) -> None:
        try:
            self.channel.send("cancelled")
        finally:
            self._finish(0)

    def _finish(self, code: int) -> None:
        """End this process, finishing a recording still in progress first. The hidden window
        stays until the event loop has returned (main() closes it): this can run while Windows is
        telling it something, and Qt goes on using it after its filters."""
        if self._done:
            return
        self._done = True
        self._progress.stop()
        if self.recorder is not None and self.recorder.state in (*ACTIVE, "finishing"):
            self.recorder.finish_now(EXIT_FINISH_SECONDS)  # reports "finished" on the way
        self.channel.close()
        self.watch.allow_shutdown()
        self._quit(code)
