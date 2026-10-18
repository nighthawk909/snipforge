"""SnipForge's side of a recording made in the recorder process (recorder_process.py).

RemoteRecorder has a Recorder's controls and signals, so the recording UI can use either. It opens
a channel only this user can reach, starts `SnipForge.exe --record <channel>` detached (so the
recording outlives a SnipForge crash), sends the settings, and passes on what the recorder reports.
What comes back is checked: the file reported must be the one asked for.
"""
from __future__ import annotations

import logging
import math
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, QProcess, QTimer, Signal

from .channel import Channel, listen, new_name, settings_to_message
from .recorder import OVER, RecordingError, Result, Settings
from .recorder_process import RECORD_FLAG

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_MS = 30_000  # a cold start of the packaged app, with antivirus scanning it, is slow
STATES = ("starting", "recording", "paused", "finishing")


def launch_recorder(args: list[str]) -> bool:
    """Start SnipForge's recorder process with `args`, detached from this one."""
    if getattr(sys, "frozen", False):  # installed build: SnipForge.exe
        program, arguments, folder = sys.executable, args, str(Path(sys.executable).parent)
    else:
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")  # no console window
        program = str(pythonw if pythonw.exists() else exe)
        arguments, folder = ["-m", "snipforge", *args], str(Path(__file__).resolve().parents[2])
    started, _pid = QProcess.startDetached(program, arguments, folder)
    return bool(started)


class RemoteRecorder(QObject):
    """One recording, made by the recorder process. start(), pause(), resume(), stop() or cancel();
    `finished`, `failed` or `cancelled` follows, as with a Recorder."""

    state_changed = Signal(str)  # "launching", then the Recorder's states
    warning = Signal(str)
    finished = Signal(object)  # Result
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, settings: Settings, *, launch: Callable[[list[str]], bool] | None = None,
                 connect_timeout_ms: int = CONNECT_TIMEOUT_MS) -> None:
        super().__init__()
        self.settings = settings
        self.state = "idle"
        self.pid = self.window_id = 0  # the recorder process, once it says hello
        self.ended_by: str | None = None  # why it ended, if not because it was stopped
        self._launch = launch or launch_recorder
        self._connect_timeout_ms = connect_timeout_ms
        self._server = None
        self.channel: Channel | None = None

    def start(self) -> None:
        """Start the recorder process; raises RecordingError if it can't be started at all."""
        if self.state != "idle":
            raise RecordingError("This recording has already started")
        name = new_name()
        try:
            self._server = listen(name)
        except OSError as exc:
            raise RecordingError(str(exc)) from exc
        self._server.newConnection.connect(self._connected)
        if not self._launch([RECORD_FLAG, name]):
            self._server.close()
            raise RecordingError("SnipForge couldn't start its recorder")
        self._set_state("launching")
        QTimer.singleShot(self._connect_timeout_ms, self, self._check_connected)

    def pause(self) -> None:
        self._send("pause")

    def resume(self) -> None:
        self._send("resume")

    def stop(self) -> None:
        if self.state == "launching":
            self.cancel()
        else:
            self._send("stop")

    def cancel(self) -> None:
        if self.state == "launching":  # closing the channel's server sends the recorder away
            self._end("cancelled")
            self.cancelled.emit()
        else:
            self._send("cancel")

    def _send(self, kind: str) -> None:
        if self.channel is not None and self.state not in OVER:
            self.channel.send(kind)

    # --- the channel -----------------------------------------------------------------------------

    def _connected(self) -> None:
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        self._server.close()  # one recorder per channel
        self.channel = Channel(socket, self)
        self.channel.message.connect(self._on_message)
        self.channel.closed.connect(self._gone)
        self.channel.send("start", settings=settings_to_message(self.settings))

    def _check_connected(self) -> None:
        if self.state == "launching":
            self._end("failed")
            self.failed.emit("SnipForge's recorder didn't start")

    def _on_message(self, message: dict) -> None:
        kind = message["type"]
        if self.state in OVER:
            return
        if kind == "hello":
            self.pid, self.window_id = _whole(message.get("pid")), _whole(message.get("window"))
        elif kind == "state" and message.get("state") in STATES:
            self._set_state(message["state"])
        elif kind == "warning":
            self.warning.emit(str(message.get("message", "")))
        elif kind == "finished":
            self._on_finished(message)
        elif kind == "failed":
            self._end("failed")
            self.failed.emit(str(message.get("message") or "The recording failed"))
        elif kind == "cancelled":
            self._end("cancelled")
            self.cancelled.emit()

    def _on_finished(self, message: dict) -> None:
        duration, stats = message.get("duration"), message.get("stats")
        if message.get("path") != str(self.settings.output) or not isinstance(duration, (int, float)) \
                or not math.isfinite(duration) or duration < 0:
            self._end("failed")
            self.failed.emit("The recorder reported a recording SnipForge didn't ask for")
            return
        ended_by = message.get("ended_by")
        self.ended_by = ended_by if isinstance(ended_by, str) else None
        self._end("finished")
        self.finished.emit(Result(self.settings.output, float(duration), stats if isinstance(stats, dict) else {}))

    def _gone(self) -> None:
        if self.state not in OVER:
            self._end("failed")
            self.failed.emit("SnipForge's recorder stopped unexpectedly")

    def _end(self, state: str) -> None:
        if self._server is not None:
            self._server.close()
        self._set_state(state)

    def _set_state(self, state: str) -> None:
        self.state = state
        self.state_changed.emit(state)


def _whole(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def ping(*, launch: Callable[[list[str]], bool] | None = None, timeout_ms: int = CONNECT_TIMEOUT_MS) -> str:
    """Start a recorder process just to have it answer; its version. RecordingError if it doesn't.
    The self-test uses it to prove the packaged app can record in a process of its own."""
    name = new_name()
    try:
        server = listen(name)
    except OSError as exc:
        raise RecordingError(str(exc)) from exc
    loop, answer, held = QEventLoop(), {}, []

    def connected():
        socket = server.nextPendingConnection()
        if socket is not None:
            channel = Channel(socket)
            held.append(channel)
            channel.message.connect(lambda m: m["type"] == "pong" and (answer.update(m), loop.quit()))
            channel.send("ping")

    server.newConnection.connect(connected)
    try:
        if not (launch or launch_recorder)([RECORD_FLAG, name]):
            raise RecordingError("SnipForge couldn't start its recorder")
        QTimer.singleShot(timeout_ms, loop, loop.quit)
        loop.exec()
    finally:
        server.close()
        for channel in held:
            channel.close()
    if "version" not in answer:
        raise RecordingError("SnipForge's recorder didn't answer")
    return str(answer["version"])
