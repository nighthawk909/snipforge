"""The countdown before a delayed snip: "Snipping in 3 seconds", with Snip now and Cancel.

It floats at the top of the monitor the pointer is on and never takes focus, so a menu or tooltip
you open during the countdown stays open. When time is up it hides, waits a moment for Windows to
take it off the screen, and only then reports finished, so it never appears in the snip.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QWidget

from .icons import icon

MAX_SECONDS = 60
SETTLE_MS = 150  # long enough for the desktop to redraw without the countdown
TOP_MARGIN = 18


class Countdown(QWidget):
    finished = Signal()  # time is up and the countdown is off the screen
    cancelled = Signal()

    def __init__(self, seconds: int, screen, settle_ms: int | None = None) -> None:
        if not 1 <= seconds <= MAX_SECONDS:
            raise ValueError(f"the delay must be 1 to {MAX_SECONDS} seconds")
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAccessibleName("Snip countdown")
        self._left = seconds
        self._screen = screen
        self._settle_ms = SETTLE_MS if settle_ms is None else settle_ms
        self._done = False
        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self.tick)

        card = QFrame(self)
        card.setObjectName("Countdown")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 8, 8, 8)
        row.setSpacing(10)
        clock = QLabel(card)
        clock.setPixmap(icon("timer").pixmap(QSize(20, 20)))
        row.addWidget(clock)
        self._label = QLabel(card)
        self._label.setObjectName("CountdownText")
        row.addWidget(self._label)
        self.now_button = self._button(card, "Snip now")
        self.now_button.clicked.connect(self.snip_now)
        row.addWidget(self.now_button)
        self.cancel_button = self._button(card, "Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        row.addWidget(self.cancel_button)
        self._update_label()

    @staticmethod
    def _button(parent, text: str) -> QToolButton:
        button = QToolButton(parent)
        button.setText(text)
        button.setFocusPolicy(Qt.NoFocus)  # clicking it mustn't take focus from the app being snipped
        button.setCursor(Qt.ArrowCursor)
        return button

    def message(self) -> str:
        return self._label.text()

    def _update_label(self) -> None:
        unit = "second" if self._left == 1 else "seconds"
        self._label.setText(f"Snipping in {self._left} {unit}")

    def start(self) -> None:
        self.adjustSize()
        area = self._screen.availableGeometry()
        self.move(area.x() + (area.width() - self.width()) // 2, area.y() + TOP_MARGIN)
        self.show()
        self._timer.start()

    def tick(self) -> None:
        if self._done:
            return
        self._left -= 1
        if self._left <= 0:
            self._finish()
        else:
            self._update_label()

    def snip_now(self) -> None:
        if not self._done:
            self._finish()

    def cancel(self) -> None:
        if self._done:
            return
        self._done = True
        self._timer.stop()
        self.hide()
        self.cancelled.emit()

    def _finish(self) -> None:
        self._done = True
        self._timer.stop()
        self.hide()
        QTimer.singleShot(self._settle_ms, self.finished.emit)
