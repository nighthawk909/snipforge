"""The recording's controls, shown beside the area while it's recorded, in the recorder process:

    [● 0:42  🎤 ▮▮▯  🔊 ▮▯   ❚❚ Pause   ■ Stop   🗑]

Pause and resume, Stop (the recording is finished and opened), and Discard, which asks again on the
same button before it throws the recording away. It's kept out of the recording, never takes the
keyboard from the app being recorded, and updates four times a second: a control bar that redrew
itself constantly once stalled the prototype's recording.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton

from ..countdown import exclude_from_capture
from ..icons import icon
from .geometry import place_beside, screen_of, to_logical
from .meter import LevelMeter
from .recorder import ACTIVE
from .recordings import duration_text

UPDATE_MS = 250
DISARM_MS = 3000  # how long Discard waits for its second click


class ControlBar(QFrame):
    def __init__(self, recorder, settings) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setObjectName("RecordBar")
        self.setAccessibleName("Recording")
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)  # it's never the active window
        self.recorder, self.settings = recorder, settings
        self._armed = False
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 4, 4)
        row.setSpacing(4)
        self.dot = QLabel("●")
        self.dot.setObjectName("RecordDot")
        self.time = QLabel("0:00")
        self.time.setObjectName("RecordTime")
        self.time.setAccessibleName("Time recorded")
        row.addWidget(self.dot)
        row.addWidget(self.time)
        self.meters = {}
        for kind, on, name, icon_name in (("microphone", settings.microphone, "Microphone", "mic"),
                                          ("system", settings.system_audio, "Computer sound", "speaker")):
            if on:
                row.addWidget(self._icon_label(icon_name, name))
                self.meters[kind] = LevelMeter(name)
                row.addWidget(self.meters[kind])
        self.pause_button = self._button("pause", " Pause", "Pause the recording")
        self.pause_button.clicked.connect(self._pause_or_resume)
        self.stop_button = self._button("stop", " Stop", "Stop and save the recording")
        self.stop_button.clicked.connect(recorder.stop)
        self.discard_button = self._button("trash", "", "Discard the recording")
        self.discard_button.setAccessibleName("Discard")
        self.discard_button.clicked.connect(self._discard)
        for button in (self.pause_button, self.stop_button, self.discard_button):
            row.addWidget(button)
        self._timer = QTimer(self, interval=UPDATE_MS)
        self._timer.timeout.connect(self.refresh)
        recorder.state_changed.connect(self._state)

    def _icon_label(self, icon_name: str, name: str) -> QLabel:
        label = QLabel()
        label.setPixmap(icon(icon_name).pixmap(QSize(16, 16)))
        label.setAccessibleName(name)
        return label

    def _button(self, icon_name: str, text: str, tip: str) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(icon(icon_name))
        button.setIconSize(QSize(16, 16))
        button.setText(text)
        button.setToolTip(tip)
        button.setAccessibleName(text.strip() or tip)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if text else Qt.ToolButtonIconOnly)
        button.setFocusPolicy(Qt.NoFocus)  # never the keyboard from the app being recorded
        button.setCursor(Qt.ArrowCursor)
        return button

    def showEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().showEvent(e)
        exclude_from_capture(self)
        self._place()
        self._timer.start()
        self.refresh()

    def _place(self) -> None:
        self.adjustSize()
        area = QRect(*self.settings.area)
        screen = screen_of(area)
        self.move(place_beside(to_logical(area, screen), self.size(), screen.availableGeometry()))

    def refresh(self) -> None:
        self.time.setText(duration_text(self.recorder.media_time()))
        levels = self.recorder.levels()
        for kind, meter in self.meters.items():
            meter.set_level(levels.get(kind))

    def _state(self, state: str) -> None:
        paused = state == "paused"
        self.pause_button.setIcon(icon("play" if paused else "pause"))
        self.pause_button.setText(" Resume" if paused else " Pause")
        self.pause_button.setToolTip("Go on recording" if paused else "Pause the recording")
        self.dot.setText("❚❚" if paused else "●")
        if state not in ACTIVE:  # finishing, or over: nothing left to control
            self._timer.stop()
            self.close()

    def _pause_or_resume(self) -> None:
        if self.recorder.state == "paused":
            self.recorder.resume()
        else:
            self.recorder.pause()

    def _discard(self) -> None:
        if self._armed:
            self.recorder.cancel()
            return
        self._armed = True
        self.discard_button.setText(" Discard?")
        self.discard_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.discard_button.setToolTip("Click again to throw the recording away")
        self._place()
        QTimer.singleShot(DISARM_MS, self, self._disarm)

    def _disarm(self) -> None:
        self._armed = False
        self.discard_button.setText("")
        self.discard_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.discard_button.setToolTip("Discard the recording")
        self._place()
