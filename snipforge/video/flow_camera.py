"""The webcam for one recording, on SnipForge's side: the camera, its bubble beside the area while you
get ready (so you can place it), and once recording its pictures shared with the recorder process
(camera_share.py), with the bubble's moves passed on so the recording follows it.

SnipForge owns all of this, not the recorder process: a window showing live video there would starve
its frame thread. The bubble's shape, size and place are remembered for next time.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import QObject, QRect, QTimer, Signal

from . import video_prefs
from .video_prefs import RecordingChoices

log = logging.getLogger(__name__)

MARGIN = 16  # physical pixels between a new bubble and the area's corner
FOLLOW_MS = 33  # while recording, the bubble's place goes to the recorder this often (if it changed)


class FlowCamera(QObject):
    failed = Signal(str)  # it couldn't start, or stopped: why, for the person

    def __init__(self, area: QRect, make_webcam=None, make_bubble=None) -> None:
        """`area` in physical desktop pixels."""
        super().__init__()
        self.area = QRect(area)
        self._make_webcam = make_webcam or _webcam
        self._make_bubble = make_bubble or _bubble
        self.webcam = self.bubble = self.writer = None
        self._device: str | None = None
        self._recorder = None
        self._sent = None  # the bubble's place last told to the recorder
        self._follow = QTimer(self, interval=FOLLOW_MS)
        self._follow.timeout.connect(self._tell_recorder)

    @property
    def on(self) -> bool:
        return self.webcam is not None

    def devices(self) -> list:
        """The cameras there are, as (id, name)."""
        return (self.webcam or self._make_webcam()).devices()

    def show(self, choices: RecordingChoices) -> None:
        """Camera on (or onto another camera) with its bubble, or off, as `choices` say."""
        if not choices.webcam:
            self.close()
            return
        if self.on and choices.webcam_id == self._device:
            return
        self.close()
        self._device = choices.webcam_id
        self.webcam = self._make_webcam()
        self.webcam.failed.connect(self._failed)
        self.bubble = self._make_bubble(self.webcam, choices.bubble_shape, choices.bubble_size)
        self.bubble.moved.connect(self._moved)
        self.bubble.shape_changed.connect(self._changed)
        self.bubble.size_changed.connect(self._changed)
        self.bubble.show()
        self._place(choices)
        self.webcam.start(choices.webcam_id)  # a camera that can't start says why through `failed`

    def _place(self, choices: RecordingChoices) -> None:
        """Where it was left last time if that's over the area (or the camera would be left out of
        the recording), else inside the area at its bottom right."""
        size = self.bubble.area_rect()
        if choices.bubble_x is not None and choices.bubble_y is not None:
            left = QRect(choices.bubble_x, choices.bubble_y, size.width(), size.height())
            if left.intersects(self.area):
                self.bubble.move_physical(choices.bubble_x, choices.bubble_y)
                return
        a = self.area
        self.bubble.move_physical(a.x() + a.width() - size.width() - MARGIN,
                                  a.y() + a.height() - size.height() - MARGIN)

    def share(self) -> dict:
        """Start sharing pictures with the recorder: its settings for the camera, or {} when it's off."""
        if not self.on:
            return {}
        from .camera_share import CameraWriter

        self.writer = CameraWriter()
        self.webcam.frame.connect(self.writer.write)
        return {"camera_share": self.writer.name, "camera_bubble": self.bubble.area_rect().getRect(),
                "camera_shape": self.bubble.shape()}

    def follow(self, recorder) -> None:
        """From now on, tell `recorder` where the bubble is (RemoteRecorder.move_bubble): as it's
        dragged, not just where it's dropped."""
        self._recorder = recorder
        if self.on:
            self._sent = (self.bubble.area_rect().getRect(), self.bubble.shape())
            self._follow.start()

    def remembered(self, choices: RecordingChoices) -> RecordingChoices:
        """`choices` with the bubble as it was left (moved, reshaped or resized since the choices were
        read); saved straight away when that happens, so read back here."""
        saved = video_prefs.load()
        return replace(choices, bubble_shape=saved.bubble_shape, bubble_size=saved.bubble_size,
                       bubble_x=saved.bubble_x, bubble_y=saved.bubble_y)

    def _moved(self, rect: QRect) -> None:
        self._tell_recorder()
        video_prefs.save(replace(video_prefs.load(), bubble_x=rect.x(), bubble_y=rect.y()))

    def _changed(self, _value: str) -> None:
        self._tell_recorder()
        video_prefs.save(replace(video_prefs.load(), bubble_shape=self.bubble.shape(),
                                 bubble_size=self.bubble.bubble_size()))

    def _tell_recorder(self) -> None:
        if self._recorder is None or self.bubble is None:
            return
        place = (self.bubble.area_rect().getRect(), self.bubble.shape())
        if place == self._sent:
            return
        self._sent = place
        try:
            self._recorder.move_bubble(*place)
        except Exception:  # the recording goes on with the bubble where it was
            log.exception("Couldn't tell the recorder where the camera's bubble went")

    def _failed(self, message: str) -> None:
        self.close()
        self.failed.emit(message)

    def close(self) -> None:
        """Camera off, bubble gone, pictures no longer shared, the recording told to leave the camera
        out. Every step is tried whatever the others do: the camera must never be left on."""
        self._follow.stop()
        webcam, bubble, writer, recorder = self.webcam, self.bubble, self.writer, self._recorder
        self.webcam = self.bubble = self.writer = self._recorder = self._sent = None
        self._device = None
        steps = []
        if recorder is not None and bubble is not None:
            steps.append(lambda: recorder.move_bubble(None, bubble.shape()))  # hide it in the recording now
        if webcam is not None:
            steps += [lambda: webcam.failed.disconnect(self._failed), webcam.stop]
        if bubble is not None:
            steps.append(bubble.close)
        if writer is not None:
            steps.append(writer.close)
        for step in steps:
            try:
                step()
            except Exception:
                log.exception("Closing the camera: a step failed; going on with the rest")


def _webcam():
    from .webcam import Webcam

    return Webcam()


def _bubble(camera, shape: str, size: str):
    from .webcam_bubble import WebcamBubble

    return WebcamBubble(camera, shape=shape, size=size)
