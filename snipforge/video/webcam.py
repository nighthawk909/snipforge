"""The webcam: Qt's camera (QCamera → QMediaCaptureSession → QVideoSink), for the bubble on screen
and for the pictures drawn into recordings.

Pictures arrive on Qt's main thread. Each is converted to an opaque QImage at most about 30 times a
second (converting costs 1-3 ms; a faster camera's extra pictures are skipped before that cost),
kept for latest(), which the frame thread reads under a lock, and sent to the bubble through
`frame`. The camera is asked for a modest format (about 640x480): a bubble never needs more, and a
bigger picture costs more to convert.

When the camera can't be used, `failed` says why in words for the person: no camera, the chosen one
unplugged, Windows' privacy setting, another app using it, or no picture at all. The camera is
switched off then. Qt reports most failures with the same vague text, so the privacy setting is read
from where Windows keeps it, and a camera that's gone is found by listing the cameras again.

Qt Multimedia is imported only when cameras are listed or opened, so SnipForge starts as fast as
before (snipforge/video/__init__.py).
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

log = logging.getLogger(__name__)

MAX_FPS = 30
JITTER = 0.8  # pictures closer than this share of 1/MAX_FPS are skipped, so a 30 fps camera loses none
FIRST_PICTURE_TIMEOUT_MS = 8000  # opening a camera takes ~0.2 s, a few seconds for some USB ones
TARGET_SIDE = 480  # pixels: enough for the largest bubble on a scaled screen
MIN_CAMERA_FPS = 24

NO_CAMERA = "No camera was found. Plug one in, or check it's switched on."
CHOSEN_MISSING = "The chosen camera isn't connected. Plug it in, or choose another camera."
CAMERA_GONE = "The camera was unplugged or switched off."
IN_USE = "Another app is using the camera. Close it, then try again."
NO_PICTURE = "The camera didn't send a picture. Another app may be using it: close that app, then try again."
WINDOWS_BLOCKED = ("Windows isn't letting apps use the camera. In Settings > Privacy & security > Camera, turn on "
                   "Camera access and Let desktop apps access your camera.")
MAC_BLOCKED = "macOS isn't letting SnipForge use the camera. Allow it in System Settings > Privacy & Security > Camera."
_IN_USE_WORDS = ("in use", "another", "busy")

# Where Windows keeps Settings > Privacy & security > Camera: "Camera access" (for everyone on this
# PC), "Let apps access your camera", and "Let desktop apps access your camera". Each says Allow or Deny.
_CONSENT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"


def blocked_message() -> str:
    return MAC_BLOCKED if sys.platform == "darwin" else WINDOWS_BLOCKED


def explain(text: str, *, present: bool, blocked: bool, started: bool) -> str:
    """What a camera failure means for the person: `text` is Qt's own words, `present` whether the
    camera is still listed, `blocked` the privacy setting, `started` whether a picture ever came."""
    if blocked:
        return blocked_message()
    if not present:
        return CAMERA_GONE
    if any(word in text.lower() for word in _IN_USE_WORDS):
        return IN_USE
    detail = f" ({text})" if text else ""
    if started:
        return f"The camera stopped working{detail}."
    return f"The camera couldn't start{detail}. Another app may be using it: close that app, then try again."


def choose_format(formats: Sequence[tuple[int, int, float, bool]]) -> int | None:
    """Which of a camera's formats (width, height, top frame rate, compressed?) to ask for: the smallest
    at least TARGET_SIDE each way, else the largest. Uncompressed and smooth ones come first, since
    decoding JPEG for every picture costs time. None when the camera lists no formats."""
    if not formats:
        return None
    indexed = list(enumerate(formats))
    smooth = [(i, f) for i, f in indexed if f[2] >= MIN_CAMERA_FPS]
    pool = [(i, f) for i, f in smooth if not f[3]] or smooth or indexed
    big = [(i, f) for i, f in pool if min(f[0], f[1]) >= TARGET_SIDE]
    if big:
        return min(big, key=lambda item: (item[1][0] * item[1][1], item[0]))[0]
    return max(pool, key=lambda item: (item[1][0] * item[1][1], -item[0]))[0]


def camera_blocked() -> bool:
    """Whether the system's privacy settings keep SnipForge from the camera."""
    if sys.platform == "win32":
        return any(value.strip().lower() == "deny" for value in _consent_values())
    return _qt_permission_denied()


def _consent_values() -> list[str]:
    import winreg

    places = ((winreg.HKEY_LOCAL_MACHINE, _CONSENT), (winreg.HKEY_CURRENT_USER, _CONSENT),
              (winreg.HKEY_CURRENT_USER, _CONSENT + r"\NonPackaged"))
    values = []
    for hive, path in places:
        try:
            with winreg.OpenKey(hive, path) as key:
                value, _kind = winreg.QueryValueEx(key, "Value")
        except OSError:
            continue  # never set: Windows' default, which allows
        values.append(str(value))
    return values


def _qt_permission_denied() -> bool:
    """macOS (and others): Qt's own permission check. Asking for permission is for the Mac phase."""
    from PySide6.QtCore import QCameraPermission, QCoreApplication, Qt

    app = QCoreApplication.instance()
    return app is not None and app.checkPermission(QCameraPermission()) == Qt.PermissionStatus.Denied


class Webcam(QObject):
    """One camera at a time: start() switches it on, stop() off. latest() is its newest picture (for
    the frame thread), `frame` brings each picture to the bubble, and `failed` says why the camera
    couldn't start or stopped, after switching it off."""

    frame = Signal(QImage)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None, *, backend=None, max_fps: int = MAX_FPS,
                 first_picture_timeout_ms: int = FIRST_PICTURE_TIMEOUT_MS,
                 now: Callable[[], float] = time.perf_counter) -> None:
        super().__init__(parent)
        self._backend = backend if backend is not None else _QtBackend()
        self._min_interval = JITTER / max_fps
        self._now = now
        self._lock = threading.Lock()
        self._latest: QImage | None = None
        self._session = None
        self._device_id: str | None = None
        self._last_picture: float | None = None
        self.pictures = 0  # converted since start()
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(first_picture_timeout_ms)
        self._watchdog.timeout.connect(self._no_picture)

    @property
    def is_active(self) -> bool:
        return self._session is not None

    @property
    def device_id(self) -> str | None:
        return self._device_id

    def devices(self) -> list[tuple[str, str]]:
        """The cameras as (id, name), Windows' default first; empty when there are none."""
        try:
            return self._backend.devices()
        except (ImportError, OSError, RuntimeError):
            log.exception("Listing the cameras failed")
            return []

    def start(self, device_id: str | None = None) -> bool:
        """Switches on Windows' default camera, or `device_id` from devices(). False if it can't, after
        `failed` says why; a failure once it's running is reported through `failed` too."""
        self.stop()
        listed = [camera_id for camera_id, _name in self.devices()]
        if not listed:
            return self._refuse(NO_CAMERA)
        chosen = listed[0] if device_id is None else device_id
        if chosen not in listed:
            return self._refuse(CHOSEN_MISSING)
        if self._backend.blocked():  # checked first, so a blocked camera is never even asked
            return self._refuse(blocked_message())
        return self._open(chosen)

    def stop(self) -> None:
        """Switches the camera off; latest() is None until a camera sends a picture again."""
        self._watchdog.stop()
        session, self._session, self._device_id = self._session, None, None
        with self._lock:
            self._latest = None
        if session is not None:
            try:
                session.close()
            except RuntimeError:
                log.exception("Closing the camera failed")

    def latest(self) -> QImage | None:
        """The newest picture (opaque RGB32), or None. Safe from any thread; the copy shares memory
        until someone draws on it, so it's cheap and can't change what others see."""
        with self._lock:
            return None if self._latest is None else QImage(self._latest)

    # --- the camera's side -----------------------------------------------------------------------

    def _open(self, device_id: str) -> bool:
        self._device_id, self.pictures, self._last_picture = device_id, 0, None
        try:
            self._session = self._backend.open(device_id, self._on_video_frame, self._on_camera_error, self)
            self._watchdog.start()
            self._session.start()  # may report a failure straight away, through _on_camera_error
        except Exception as exc:  # the camera driver is out of our hands; a failure mustn't take the app down
            log.exception("Opening the camera failed")
            self._fail(str(exc))
            return False
        return self._session is not None

    def _on_video_frame(self, video_frame) -> None:
        if self._session is None:
            return  # one still on its way after stop()
        now = self._now()
        if self._last_picture is not None and now - self._last_picture < self._min_interval:
            return
        self._last_picture = now
        image = video_frame.toImage()  # a new image in its own memory, converted from the camera's
        if image.isNull():
            return
        image = image.convertToFormat(QImage.Format.Format_RGB32)  # the quickest to draw into frames
        self._watchdog.stop()
        with self._lock:
            self._latest = image
        self.pictures += 1
        self.frame.emit(image)

    def _on_camera_error(self, text: str) -> None:
        if self._session is not None:  # else stopped, or already reported
            self._fail(text)

    def _fail(self, text: str) -> None:
        present = self._device_id in [camera_id for camera_id, _name in self.devices()]
        self._give_up(explain(text, present=present, blocked=self._backend.blocked(), started=self.pictures > 0),
                      text)

    def _no_picture(self) -> None:
        if self._session is not None and self.pictures == 0:
            self._give_up(blocked_message() if self._backend.blocked() else NO_PICTURE, "no picture")

    def _give_up(self, message: str, detail: str) -> None:
        log.warning("The camera failed: %s (%s)", message, detail)
        self.stop()
        self.failed.emit(message)

    def _refuse(self, message: str) -> bool:
        log.info("The camera can't start: %s", message)
        self.failed.emit(message)
        return False


class _QtBackend:
    """The real cameras, through Qt Multimedia (imported here, on first use)."""

    def devices(self) -> list[tuple[str, str]]:
        from PySide6.QtMultimedia import QMediaDevices

        default = _device_id(QMediaDevices.defaultVideoInput())
        found = [(_device_id(device), device.description()) for device in QMediaDevices.videoInputs()]
        # Default first, by comparing with defaultVideoInput(): QCameraDevice.isDefault() can be False
        # even for a PC's only camera.
        return sorted(found, key=lambda item: item[0] != default)

    def blocked(self) -> bool:
        return camera_blocked()

    def open(self, device_id: str, on_frame: Callable, on_error: Callable[[str], None], parent: QObject):
        return _QtCamera(device_id, on_frame, on_error, parent)


def _device_id(device) -> str:
    return "" if device.isNull() else bytes(device.id().data()).decode("utf-8", "replace")


def _best_format(device):
    from PySide6.QtMultimedia import QVideoFrameFormat

    formats = device.videoFormats()
    jpeg = QVideoFrameFormat.PixelFormat.Format_Jpeg
    index = choose_format([(f.resolution().width(), f.resolution().height(), f.maxFrameRate(), f.pixelFormat() == jpeg)
                           for f in formats])
    return None if index is None else formats[index]


class _QtCamera(QObject):
    """One camera switched on. Its pictures go to on_frame and anything that stops it to on_error, both
    on the main thread, and neither once close() has been called."""

    def __init__(self, device_id: str, on_frame: Callable, on_error: Callable[[str], None], parent: QObject) -> None:
        super().__init__(parent)
        from PySide6 import QtMultimedia as M

        device = next((d for d in M.QMediaDevices.videoInputs() if _device_id(d) == device_id), None)
        if device is None:
            raise LookupError("the camera isn't connected")
        self._device_id, self._on_frame, self._on_error = device_id, on_frame, on_error
        self._closing = False
        # Qt children, freed with this object in a safe order: the session first, which lets go of the rest.
        self._session = M.QMediaCaptureSession(self)
        self._camera = M.QCamera(device, self)
        self._sink = M.QVideoSink(self)
        self._devices = M.QMediaDevices(self)
        chosen = _best_format(device)
        if chosen is not None:
            self._camera.setCameraFormat(chosen)
        self._session.setCamera(self._camera)
        self._session.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._frame)
        self._camera.errorOccurred.connect(self._error)
        self._camera.activeChanged.connect(self._active_changed)
        self._devices.videoInputsChanged.connect(self._devices_changed)

    def start(self) -> None:
        self._camera.start()

    def close(self) -> None:
        self._closing = True
        self._camera.stop()
        self.deleteLater()

    def _frame(self, video_frame) -> None:
        if not self._closing:
            self._on_frame(video_frame)

    def _error(self, _error, text: str) -> None:
        self._report(text or "camera error")

    def _active_changed(self, active: bool) -> None:
        if not active:  # switched off by something other than close(): Windows, or the driver
            self._report("")

    def _devices_changed(self) -> None:
        from PySide6.QtMultimedia import QMediaDevices

        if all(_device_id(device) != self._device_id for device in QMediaDevices.videoInputs()):
            self._report("")

    def _report(self, text: str) -> None:
        if not self._closing:
            self._on_error(text)
