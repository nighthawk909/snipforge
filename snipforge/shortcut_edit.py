"""A field that records one key combination (click it, press the keys), with a button to clear it.
Settings > Capture uses one per shortcut of your own."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QKeySequenceEdit, QToolButton, QWidget

from .icons import icon


class ShortcutEdit(QWidget):
    changed = Signal(str)  # the combination in Qt's portable text, or "" when cleared

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.edit = QKeySequenceEdit(self)
        self.edit.setMaximumSequenceLength(1)  # one combination, not a sequence
        self.edit.setMinimumWidth(170)
        self.edit.setToolTip("Click, then press the keys you want")
        self.edit.editingFinished.connect(self._finished)
        row.addWidget(self.edit)
        self.clear_button = QToolButton(self)
        self.clear_button.setIcon(icon("close"))
        self.clear_button.setToolTip("No shortcut")
        self.clear_button.setAccessibleName("Clear the shortcut")
        self.clear_button.setFocusPolicy(Qt.TabFocus)
        self.clear_button.clicked.connect(self.clear)
        row.addWidget(self.clear_button)
        self._text = ""
        self.set_text(text)
        self.setFocusProxy(self.edit)

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        """Show `text` without announcing it (for what's saved, or putting back a refused one)."""
        self._text = text
        self.edit.blockSignals(True)
        self.edit.setKeySequence(QKeySequence(text, QKeySequence.PortableText))
        self.edit.blockSignals(False)
        self.clear_button.setEnabled(bool(text))

    def clear(self) -> None:
        if self._text:
            self.set_text("")
            self.changed.emit("")

    def setAccessibleName(self, name: str) -> None:  # noqa: N802 - Qt API
        super().setAccessibleName(name)
        self.edit.setAccessibleName(name)

    def _finished(self) -> None:
        text = self.edit.keySequence().toString(QKeySequence.PortableText)
        if text != self._text:
            self._text = text
            self.clear_button.setEnabled(bool(text))
            self.changed.emit(text)
