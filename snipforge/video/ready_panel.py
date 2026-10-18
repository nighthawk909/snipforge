"""Ready to record: after the area is chosen, a small panel beside it says what goes into the recording
and starts it.

    [Microphone ▾ ▮▮▯] [Computer sound ▮▯] [Pointer ▾] [3 s ▾]  [● Record] [✕]

Live meters show each sound source is heard (or why it can't be: a blocked microphone says so), the
microphone's menu picks the device, Pointer picks its highlight and click rings, and the countdown
runs before recording begins. Enter records, Esc cancels. The choices are remembered (video_prefs).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QVBoxLayout

from ..icons import icon
from .geometry import place_beside
from .levels import LevelPreview
from .meter import LevelMeter
from .video_prefs import COUNTDOWNS, RecordingChoices

METER_MS = 50


def countdown_label(seconds: int) -> str:
    return "No countdown" if seconds == 0 else f"{seconds} s countdown"


class ReadyPanel(QFrame):
    record = Signal(object)  # RecordingChoices: start now
    cancelled = Signal()

    def __init__(self, area: QRect, screen, choices: RecordingChoices, levels: LevelPreview,
                 devices: Callable[[], list] | None = None) -> None:
        """`area` in Qt's coordinates; `devices()` lists microphones (wasapi.devices)."""
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setObjectName("RecordPanel")
        self.setAccessibleName("Ready to record")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.choices, self.levels = choices, levels
        self._done = False
        self._devices = devices or _microphones
        self._area, self._screen = QRect(area), screen
        column = QVBoxLayout(self)
        column.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        row.setSpacing(4)
        column.addLayout(row)
        self._sources(row)
        self._options(row)
        self._actions(row)
        self.problem = QLabel()
        self.problem.setObjectName("RecordProblem")
        self.problem.setWordWrap(True)
        self.problem.hide()
        column.addWidget(self.problem)
        self._meters = QTimer(self, interval=METER_MS)
        self._meters.timeout.connect(self._show_levels)
        self._listen()

    # --- building ---------------------------------------------------------------------------------

    def _sources(self, row: QHBoxLayout) -> None:
        self.mic_button = self._toggle("mic", " Microphone", "Record your microphone", self.choices.microphone)
        self.mic_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.mic_menu = QMenu(self.mic_button)
        self.mic_menu.setAccessibleName("Microphone")
        self.mic_menu.aboutToShow.connect(self._fill_mic_menu)
        self.mic_button.setMenu(self.mic_menu)
        self.mic_button.toggled.connect(lambda on: self._change(microphone=on))
        self.mic_meter = LevelMeter("Microphone")
        self.system_button = self._toggle("speaker", " Computer sound", "Record what the computer plays",
                                          self.choices.system_audio)
        self.system_button.toggled.connect(lambda on: self._change(system_audio=on))
        self.system_meter = LevelMeter("Computer sound")
        for widget in (self.mic_button, self.mic_meter, self.system_button, self.system_meter):
            row.addWidget(widget)

    def _options(self, row: QHBoxLayout) -> None:
        self.pointer_button = self._menu_button("pointer_click", " Pointer", "The pointer, and what's drawn with it")
        self.pointer_actions = {}
        for name, text in (("pointer", "Show the pointer"), ("highlight", "Highlight it"),
                           ("clicks", "Show clicks")):
            action = self.pointer_button.menu().addAction(text)
            action.setCheckable(True)
            action.setChecked(getattr(self.choices, name))
            action.toggled.connect(lambda on, n=name: self._change(**{n: on}))
            self.pointer_actions[name] = action
        self.countdown_button = self._menu_button("timer", "", "Wait before recording begins")
        group = QActionGroup(self.countdown_button)
        self.countdown_actions = {}
        for seconds in COUNTDOWNS:
            action = self.countdown_button.menu().addAction(countdown_label(seconds))
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda _=False, s=seconds: self._set_countdown(s))
            self.countdown_actions[seconds] = action
        self._set_countdown(self.choices.countdown)
        row.addWidget(self.pointer_button)
        row.addWidget(self.countdown_button)

    def _actions(self, row: QHBoxLayout) -> None:
        self.record_button = QPushButton(icon("record", "#ffffff"), " Record")
        self.record_button.setObjectName("RecordStart")
        self.record_button.setIconSize(QSize(16, 16))
        self.record_button.setToolTip("Start recording (Enter)")
        self.record_button.clicked.connect(self._record)
        self.cancel_button = self._button("close", "", "Cancel (Esc)")
        self.cancel_button.setAccessibleName("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        row.addSpacing(6)
        row.addWidget(self.record_button)
        row.addWidget(self.cancel_button)

    def _button(self, icon_name: str, text: str, tip: str) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(icon(icon_name))
        button.setIconSize(QSize(18, 18))
        button.setText(text)
        button.setToolTip(tip)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if text else Qt.ToolButtonIconOnly)
        return button

    def _toggle(self, icon_name: str, text: str, tip: str, on: bool) -> QToolButton:
        button = self._button(icon_name, text, tip)
        button.setAccessibleName(text.strip())
        button.setCheckable(True)
        button.setChecked(on)
        return button

    def _menu_button(self, icon_name: str, text: str, tip: str) -> QToolButton:
        button = self._button(icon_name, text, tip)
        button.setAccessibleName(text.strip() or tip)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setPopupMode(QToolButton.InstantPopup)
        button.setMenu(QMenu(button))
        return button

    # --- choices ----------------------------------------------------------------------------------

    def _fill_mic_menu(self) -> None:
        self.mic_menu.clear()
        group = QActionGroup(self.mic_menu)
        options = [(None, "Windows' default microphone")]
        try:
            options += [(d.id, d.name) for d in self._devices()]
        except OSError:
            pass  # only the default, then
        for device_id, name in options:
            action = self.mic_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(device_id == self.choices.microphone_id)
            group.addAction(action)
            action.triggered.connect(lambda _=False, d=device_id: self._change(microphone=True, microphone_id=d))

    def _set_countdown(self, seconds: int) -> None:
        self.countdown_actions[seconds].setChecked(True)
        self.countdown_button.setText(f" {countdown_label(seconds)}")
        self._change(countdown=seconds)

    def _change(self, **changes) -> None:
        before = self.choices
        self.choices = replace(before, **changes)
        if self.mic_button.isChecked() != self.choices.microphone:
            self.mic_button.setChecked(self.choices.microphone)  # picking a device switches it on
        sound = ("microphone", "microphone_id", "system_audio")
        if any(getattr(before, name) != getattr(self.choices, name) for name in sound):
            self._listen()

    def _listen(self) -> None:
        self.levels.set_source("microphone", self.choices.microphone, self.choices.microphone_id)
        self.levels.set_source("system", self.choices.system_audio)
        problems = [f"{'Microphone' if kind == 'microphone' else 'Computer sound'}: {why}"
                    for kind, why in self.levels.problems.items()]
        self.problem.setText("\n".join(problems))
        self.problem.setVisible(bool(problems))
        self.adjustSize()

    def _show_levels(self) -> None:
        levels = self.levels.levels()
        self.mic_meter.set_level(levels["microphone"])
        self.system_meter.set_level(levels["system"])

    # --- showing and ending -----------------------------------------------------------------------

    def showEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().showEvent(e)
        self.adjustSize()
        self.move(place_beside(self._area, self.size(), self._screen.availableGeometry()))
        self._meters.start()
        self.record_button.setFocus()

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt API
        if e.key() == Qt.Key_Escape:
            self.cancel()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._record()
        else:
            super().keyPressEvent(e)

    def _record(self) -> None:
        if not self._done:
            self._finish()
            self.record.emit(self.choices)

    def cancel(self) -> None:
        if not self._done:
            self._finish()
            self.cancelled.emit()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        self._meters.stop()
        self.levels.close()  # the recorder opens the devices itself
        self.close()


def _microphones() -> list:
    from . import wasapi

    return wasapi.devices(wasapi.MICROPHONE)
