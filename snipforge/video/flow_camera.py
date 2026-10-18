"""The webcam for one recording, on SnipForge's side: the camera, its bubble beside the area while you
get ready (so you can place it), and once recording its pictures shared with the recorder process
(camera_share.py), with the bubble's moves passed on so the recording follows it.

SnipForge owns all of this, not the recorder process: a window showing live video there would starve
its frame thread. The bubble's shape, size and place are remembered for next time.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import QObject, QRect, Signal

from . import video_prefs
from .video_prefs import RecordingChoices

log = logging.getLogger(__name__)

MARGIN = 16  # physical pixels between a new bubble and the area's corner


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
        """Where it was left last time, or inside the area at its bottom right."""
        if choices.bubble_x is not None and choices.bubble_y is not None:
            self.bubble.move_physical(choices.bubble_x, choices.bubble_y)
            return
        size = self.bubble.area_rect()
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
        """From now on, tell `recorder` when the bubble moves (RemoteRecorder.move_bubble)."""
        self._recorder = recorder

    def _moved(self, rect: QRect) -> None:
        video_prefs.save(replace(video_prefs.load(), bubble_x=rect.x(), bubble_y=rect.y()))
        self._tell_recorder()

    def _changed(self, _value: str) -> None:
        video_prefs.save(replace(video_prefs.load(), bubble_shape=self.bubble.shape(),
                                 bubble_size=getattr(self.bubble, "bubble_size", lambda: "medium")()))
        self._tell_recorder()

    def _tell_recorder(self) -> None:
        if self._recorder is not None and self.bubble is not None:
            try:
                self._recorder.move_bubble(self.bubble.area_rect().getRect(), self.bubble.shape())
            except Exception:  # the recording goes on with the bubble where it was
                log.exception("Couldn't tell the recorder where the camera's bubble went")

    def _failed(self, message: str) -> None:
        self.close()
        self.failed.emit(message)

    def close(self) -> None:
        if self.webcam is not None:
            self.webcam.failed.disconnect(self._failed)
            self.webcam.stop()
        if self.bubble is not None:
            self.bubble.close()
        if self.writer is not None:
            self.writer.close()  # the recorder keeps the last picture it read
        self.webcam = self.bubble = self.writer = self._recorder = None
        self._device = None


def _webcam():
    from .webcam import Webcam

    return Webcam()


def _bubble(camera, shape: str, size: str):
    from .webcam_bubble import WebcamBubble

    return WebcamBubble(camera, shape=shape, size=size)
