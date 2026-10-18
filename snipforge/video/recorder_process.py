"""The recorder process: `SnipForge.exe --record <channel>`, started by SnipForge for each recording.

Recording in a process of its own means nothing else SnipForge does (an editor busy with a big
image, a crash) can stall or lose a recording. It connects back to SnipForge over the channel
(channel.py), says hello, gets the recording's settings, records, reports how it's going, and
exits once the file is finished. Windows going to sleep or ending the session finishes the file
there and then (power.py), and so does SnipForge going away: until the recording controls live in
this process (step 6), SnipForge is the only way to stop it.

    SnipForge                              recorder process
      start ─► launches it ──────────────► hello {pid, window, version}
      settings ─────────── start ────────►  Recorder.start()
                ◄───────── state, warning ─
      pause / resume / stop / cancel ────►
                ◄──────── finished {path, duration, stats, ended_by} | failed | cancelled
                                            exits
"""
from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import QCoreApplication, QObject, QTimer

from .. import __version__
from .channel import Channel, connect, settings_from_message
from .power import SessionWatch
from .recorder import ACTIVE, Recorder, RecordingError

log = logging.getLogger(__name__)

RECORD_FLAG = "--record"
NAME_PREFIX = "SnipForge-recorder-"
CONNECT_TIMEOUT_MS = 10_000
START_TIMEOUT_MS = 15_000  # from hello to being told what to record
SLEEP_FINISH_SECONDS = 1.5  # Windows allows about 2 s before it sleeps
END_FINISH_SECONDS = 4.0  # and about 5 s before it offers to end an app that holds up shutdown
COMMANDS = ("pause", "resume", "stop", "cancel")


def channel_name(argv: list[str]) -> str | None:
    """The channel name after --record, if it looks like one SnipForge makes."""
    try:
        name = argv[argv.index(RECORD_FLAG) + 1]
    except (ValueError, IndexError):
        return None
    ok = name.startswith(NAME_PREFIX) and len(name) <= 256 and not any(c in name for c in '\\/:*?"<>|')
    return name if ok else None


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
    _child = RecorderChild(Channel(socket), SessionWatch())  # noqa: F841 - lives until the app exits
    return app.exec()


class RecorderChild(QObject):
    """The recorder process's side of the channel: runs one Recorder as SnipForge says."""

    def __init__(self, channel: Channel, watch, *, make_recorder=None, quit=None) -> None:
        super().__init__()
        self.channel, self.watch = channel, watch
        self.recorder = None
        self.ended_by: str | None = None  # "sleep", "session end", "SnipForge closed"
        self._make_recorder = make_recorder or Recorder
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

    # --- from SnipForge --------------------------------------------------------------------------

    def _on_message(self, message: dict) -> None:
        kind = message["type"]
        if kind == "start" and self.recorder is None:
            self._start(message.get("settings"))
        elif kind in COMMANDS and self.recorder is not None:
            getattr(self.recorder, kind)()
        elif kind == "ping":
            self.channel.send("pong", version=__version__)
            self._finish(0)
        elif kind == "quit" and self.recorder is None:
            self._finish(0)
        else:
            log.warning("The recorder ignored %r", kind)

    def _start(self, data) -> None:
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
            recorder.start()
        except RecordingError as exc:
            self._report_failure(str(exc))

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
            self.recorder.finish_now(SLEEP_FINISH_SECONDS)

    def _ending_soon(self) -> None:
        if self.recorder is not None and self.recorder.state in ACTIVE:
            self.watch.block_shutdown("Saving your screen recording")  # shown if we hold Windows up

    def _ending(self) -> None:
        if self.recorder is not None and self.recorder.state in (*ACTIVE, "finishing"):
            self.ended_by = "session end"
            self.recorder.finish_now(END_FINISH_SECONDS)
        self.watch.allow_shutdown()

    # --- to SnipForge ----------------------------------------------------------------------------

    def _finished(self, result) -> None:
        self.channel.send("finished", path=str(result.path), duration=result.duration, stats=result.stats,
                          ended_by=self.ended_by)
        self._finish(0)

    def _report_failure(self, message: str) -> None:
        self.channel.send("failed", message=message)
        self._finish(1)

    def _cancelled(self) -> None:
        self.channel.send("cancelled")
        self._finish(0)

    def _finish(self, code: int) -> None:
        if self._done:
            return
        self._done = True
        self.channel.close()
        self.watch.close()
        self._quit(code)
