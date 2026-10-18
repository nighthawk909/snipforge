"""One recording, from SnipForge's side: from the area chosen to the file saved.

    area chosen ─► outline + ready panel ─► Record ─► recorder process starts, held ─┐
                                                       countdown runs meanwhile ──────┴─► go
    recording (the control bar is in the recorder process) ─► stop ─► "Recording saved" + editor

The recorder process opens its devices and warms up its encoder while the countdown runs, so the
recording begins as the countdown ends. Stop (the tray, the shortcut) is passed on; quitting
SnipForge stops the recording too, and the recorder finishes the file.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRect, Signal

from . import video_prefs
from .area_frame import AreaFrame
from .cursor import PointerEffects
from .geometry import to_logical
from .levels import LevelPreview
from .recorder import RecordingError, Settings
from .recordings import duration_text, new_recording

log = logging.getLogger(__name__)

ENDED_BY = {"sleep": "Windows went to sleep", "session end": "Windows was signing out or shutting down",
            "SnipForge closed": "SnipForge was closed"}


class RecordFlow(QObject):
    """`area` in physical desktop pixels, on `screen`. `notify(text)` tells the person something;
    `open_video(path)` shows a finished recording."""

    ended = Signal()

    def __init__(self, area: QRect, screen, folder: Path, *, notify: Callable[[str], None],
                 open_video: Callable[[Path], None], make_recorder=None, make_countdown=None,
                 levels: LevelPreview | None = None, devices=None) -> None:
        super().__init__()
        self.area, self.screen, self.folder = QRect(area), screen, Path(folder)
        self._notify, self._open_video = notify, open_video
        self._make_recorder = make_recorder or _remote_recorder
        self._make_countdown = make_countdown or _countdown
        self._levels = levels or LevelPreview()
        self._devices = devices
        self.panel = self.frame = self.recorder = self.countdown = None
        self.state = "ready"  # then "counting down", "recording", "ended"

    @property
    def recording(self) -> bool:
        """A recorder process is under way (counting down or recording)."""
        return self.state in ("counting down", "recording")

    def start(self) -> None:
        from .ready_panel import ReadyPanel

        logical = to_logical(self.area, self.screen)
        self.frame = AreaFrame(logical)
        self.frame.show()
        self.panel = ReadyPanel(logical, self.screen, video_prefs.load(), self._levels, self._devices)
        self.panel.record.connect(self._record)
        self.panel.cancelled.connect(lambda: self._end(None))
        self.panel.show()
        self.panel.activateWindow()

    def stop(self) -> None:
        """The tray's Stop, the shortcut again, or SnipForge quitting: end it, keeping what was recorded."""
        if self.state == "ready" and self.panel is not None:
            self.panel.cancel()
        elif self.state == "counting down":
            self._drop_countdown()
            self.recorder.cancel()
        elif self.state == "recording":
            self.recorder.stop()

    # --- recording --------------------------------------------------------------------------------

    def _record(self, choices: video_prefs.RecordingChoices) -> None:
        self.panel = None
        video_prefs.save(choices)
        try:
            settings = self._settings(choices)
            self.recorder = recorder = self._make_recorder(settings)
            recorder.finished.connect(self._finished)
            recorder.failed.connect(self._failed)
            recorder.cancelled.connect(lambda: self._end(None))
            recorder.warning.connect(self._notify)
            recorder.start(hold=choices.countdown > 0)
        except (OSError, ValueError, RecordingError) as exc:
            log.warning("The recording couldn't start: %s", exc)
            self._end(f"The recording couldn't start: {exc}")
            return
        if choices.countdown:
            self.state = "counting down"
            self.countdown = self._make_countdown(choices.countdown, self.screen)
            self.countdown.finished.connect(self._go)
            self.countdown.cancelled.connect(self.stop)
            self.countdown.start()
        else:
            self.state = "recording"

    def _settings(self, choices: video_prefs.RecordingChoices) -> Settings:
        a = self.area
        return Settings(area=(a.x(), a.y(), a.width(), a.height()), output=new_recording(self.folder),
                        microphone=choices.microphone, microphone_id=choices.microphone_id,
                        system_audio=choices.system_audio,
                        effects=PointerEffects(choices.pointer, choices.highlight, choices.clicks))

    def _go(self) -> None:
        self._drop_countdown()
        if self.state == "counting down":
            self.state = "recording"
            self.recorder.go()

    def _drop_countdown(self) -> None:
        if self.countdown is not None:
            self.countdown.blockSignals(True)
            self.countdown.close()
            self.countdown.deleteLater()
            self.countdown = None

    # --- endings ----------------------------------------------------------------------------------

    def _finished(self, result) -> None:
        why = ENDED_BY.get(getattr(self.recorder, "ended_by", None) or "")
        note = f"Recording saved ({duration_text(result.duration)})"
        self._end(f"{note}: it stopped because {why}" if why else note)
        try:
            self._open_video(result.path)
        except Exception:  # the recording is saved whatever happens to the window showing it
            log.exception("Couldn't open the recording %s", result.path)

    def _failed(self, message: str) -> None:
        self._end(f"The recording failed: {message}")

    def _end(self, note: str | None) -> None:
        if self.state == "ended":
            return
        self.state = "ended"
        self._drop_countdown()
        self._levels.close()
        if self.frame is not None:
            self.frame.close()
            self.frame = None
        if note:
            self._notify(note)
        self.ended.emit()


def _remote_recorder(settings: Settings):
    from .remote_recorder import RemoteRecorder

    return RemoteRecorder(settings)


def _countdown(seconds: int, screen):
    from ..countdown import Countdown

    return Countdown(seconds, screen, recording=True)
