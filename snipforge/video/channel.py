"""The channel between SnipForge and its recorder process: one JSON object per line over a local
socket (a named pipe on Windows), and the recording's settings as such an object.

Each recording gets a random socket name of its own (no user name in it: any local user can list
pipe names) that only this Windows user can open (QLocalServer.UserAccessOption). Whatever arrives
is checked before it's used: a line that isn't a JSON object with a "type" is logged and skipped,
and one longer than MAX_LINE closes the channel, since nothing either side sends comes near it.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

MAX_LINE = 64 * 1024
WRITE_TIMEOUT_MS = 2000
NAME = re.compile(r"SnipForge-recorder-[0-9a-f]{32}")


def new_name() -> str:
    """A socket name for one recording."""
    return f"SnipForge-recorder-{secrets.token_hex(16)}"


def is_name(text: str) -> bool:
    """Whether `text` is a socket name new_name() makes."""
    return bool(NAME.fullmatch(text))


def listen(name: str) -> QLocalServer:
    """A server on `name` that only this user can connect to; OSError if it can't listen."""
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(name):
        raise OSError(f"SnipForge couldn't open a channel to its recorder: {server.errorString()}")
    return server


class Channel(QObject):
    """Messages both ways over a connected QLocalSocket. `message` gets each one as a dict with a
    "type"; `closed` follows when the other side goes away (or sends something unreadable)."""

    message = Signal(dict)
    closed = Signal()

    def __init__(self, socket: QLocalSocket, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.socket = socket
        socket.setParent(self)
        self._buffer = b""
        self._open = True
        socket.readyRead.connect(self._read)
        socket.disconnected.connect(self._gone)
        if socket.bytesAvailable():  # sent before we were listening: read once the caller has connected
            QTimer.singleShot(0, self, self._read)

    @property
    def is_open(self) -> bool:
        return self._open

    def send(self, kind: str, **fields) -> bool:
        """Send {"type": kind, **fields}; False if the channel is closed."""
        if not self._open:
            return False
        line = json.dumps({"type": kind, **fields}, separators=(",", ":")).encode() + b"\n"
        self.socket.write(line)
        self.socket.flush()
        return True

    def flush(self) -> None:
        """Wait (briefly) until everything sent has been handed over, e.g. before quitting."""
        if self._open and self.socket.bytesToWrite():
            self.socket.waitForBytesWritten(WRITE_TIMEOUT_MS)

    def close(self) -> None:
        if self._open:
            self.flush()
            self.socket.disconnectFromServer()
        self._gone()

    def _read(self) -> None:
        if not self._open:
            return
        self._buffer += bytes(self.socket.readAll())
        *lines, self._buffer = self._buffer.split(b"\n")
        if len(self._buffer) > MAX_LINE or any(len(line) > MAX_LINE for line in lines):
            log.warning("The recorder channel got a message too long to be real; closing it")
            self.close()
            return
        for line in lines:
            message = _parse(line)
            if message is not None and self._open:
                self.message.emit(message)

    def _gone(self) -> None:
        if self._open and self.socket.bytesAvailable():
            self._read()  # its last words: a "cancel" sent just before closing must still count
        if self._open:
            self._open = False
            self.closed.emit()


def _parse(line: bytes) -> dict | None:
    if not line.strip():
        return None
    try:
        message = json.loads(line)
    except (ValueError, RecursionError):  # not UTF-8 or JSON; absurdly long numbers; deep nesting
        message = None
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        log.warning("The recorder channel skipped something that isn't a message: %.80r", line)
        return None
    return message


def connect(name: str, timeout_ms: int) -> QLocalSocket:
    """A socket connected to the server `name`; OSError if it isn't there."""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(timeout_ms):
        raise OSError(f"Couldn't reach SnipForge: {socket.errorString()}")
    return socket


# --- the recording's settings, as a message ---------------------------------------------------------

def settings_to_message(settings) -> dict:
    """recorder.Settings as plain JSON values."""
    e = settings.effects
    return {"area": list(settings.area), "output": str(settings.output), "fps": settings.fps,
            "max_size": list(settings.max_size) if settings.max_size else None,
            "microphone": settings.microphone, "microphone_id": settings.microphone_id,
            "system_audio": settings.system_audio, "speakers_id": settings.speakers_id,
            "effects": {"pointer": e.pointer, "highlight": e.highlight, "clicks": e.clicks},
            "camera": {"share": settings.camera_share, "shape": settings.camera_shape,
                       "bubble": list(settings.camera_bubble) if settings.camera_bubble else None}
            if settings.camera_share else None}


def settings_from_message(data) -> object:
    """recorder.Settings from settings_to_message's form; ValueError if anything in it is off."""
    from .cursor import PointerEffects
    from .recorder import Settings

    if not isinstance(data, dict):
        raise ValueError("The recording's settings are missing")
    output = _output_path(data.get("output"))
    effects = data.get("effects") or {}
    if not isinstance(effects, dict):
        raise ValueError("The recording's pointer settings must be on or off")
    flags = {name: effects.get(name, True) for name in ("pointer", "highlight", "clicks")}
    for name, value in [*flags.items(), ("microphone", data.get("microphone")),
                        ("system_audio", data.get("system_audio"))]:
        if not isinstance(value, bool):
            raise ValueError(f"The recording's {name} setting must be on or off")
    for name in ("microphone_id", "speakers_id"):
        if data.get(name) is not None and (not isinstance(data[name], str) or len(data[name]) > 512):
            raise ValueError(f"The recording's {name} isn't a device id")
    area, max_size = data.get("area"), data.get("max_size")
    if not isinstance(area, list) or not (max_size is None or isinstance(max_size, list)):
        raise ValueError("The recording's area must be a list of numbers")
    return Settings(area=tuple(area), output=output, fps=data.get("fps"),
                    max_size=tuple(max_size) if max_size is not None else None,
                    microphone=data["microphone"], microphone_id=data.get("microphone_id"),
                    system_audio=data["system_audio"], speakers_id=data.get("speakers_id"),
                    effects=PointerEffects(**flags), **_camera(data.get("camera")))


def _camera(camera) -> dict:
    """The webcam's settings from a message (checked by Settings itself); none when it's off."""
    if camera is None:
        return {}
    if not isinstance(camera, dict) or not isinstance(camera.get("share"), str) \
            or not isinstance(camera.get("shape", "circle"), str):
        raise ValueError("The recording's camera settings make no sense")
    bubble = camera.get("bubble")
    if bubble is not None and not isinstance(bubble, list):
        raise ValueError("The recording's camera settings make no sense")
    return {"camera_share": camera["share"], "camera_shape": camera.get("shape", "circle"),
            "camera_bubble": tuple(bubble) if bubble is not None else None}


def _output_path(output) -> Path:
    """The recording's file: an .mp4 on a local or mapped drive, named plainly. Not a network share
    or a device or extended path (anything starting with two slashes), not climbing out with "..",
    and no ":" after the drive (that names an alternate data stream on NTFS)."""
    path = Path(output) if isinstance(output, str) and len(output) <= 1024 else None
    if path is None or output.startswith(("\\\\", "//")) or "://" in output or ".." in path.parts \
            or ":" in output[len(path.drive):] or not path.is_absolute() or path.suffix.lower() != ".mp4":
        raise ValueError(f"A recording can't be saved as {output!r}")
    return path
