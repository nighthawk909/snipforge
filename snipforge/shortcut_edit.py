"""A field that records one key combination (click it, press the keys), with a button to clear it.
Settings > Capture uses one per shortcut of your own.

Only a finished combination or the clear button changes the shortcut: a lone Ctrl or Win, or
leaving the field halfway through, puts back what was there. Esc cancels, Backspace or Delete
clears."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QKeySequenceEdit, QLineEdit, QToolButton, QWidget

from .icons import icon
from .shortcuts import label

_MODIFIER_KEYS = {Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr}


class _Recorder(QKeySequenceEdit):
    editing = Signal(bool)  # True while it has the keyboard
    cancelled = Signal()  # Esc
    cleared = Signal()  # Backspace or Delete on their own

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt API
        if e.modifiers() in (Qt.NoModifier, Qt.KeypadModifier):
            if e.key() == Qt.Key_Escape:
                self.cancelled.emit()
                return
            if e.key() in (Qt.Key_Backspace, Qt.Key_Delete):
                self.cleared.emit()
                return
        super().keyPressEvent(e)

    def focusInEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().focusInEvent(e)
        self.editing.emit(True)

    def focusOutEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().focusOutEvent(e)  # may finish the combination first
        self.editing.emit(False)


class ShortcutEdit(QWidget):
    changed = Signal(str)  # the combination in Qt's portable text, or "" when cleared
    editing = Signal(bool)  # the field has the keyboard (shortcuts should step aside meanwhile)

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.edit = _Recorder(self)
        self.edit.setMaximumSequenceLength(1)  # one combination, not a sequence
        self.edit.setMinimumWidth(170)
        self.edit.setToolTip("Click, then press the keys you want. Esc cancels, Backspace clears.")
        self.edit.editingFinished.connect(self._finished)
        self.edit.cancelled.connect(self._cancel)
        self.edit.cleared.connect(self.clear)
        self.edit.editing.connect(self._editing)
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
        self._show_label()

    def clear(self) -> None:
        if self._text:
            self.set_text("")
            self.changed.emit("")
        else:
            self.set_text("")

    def setAccessibleName(self, name: str) -> None:  # noqa: N802 - Qt API
        super().setAccessibleName(name)
        self.edit.setAccessibleName(name)

    def _cancel(self) -> None:
        self.set_text(self._text)
        self.edit.clearFocus()

    def _editing(self, on: bool) -> None:
        if not on:
            self._restore_if_unfinished()
        self.editing.emit(on)

    def _restore_if_unfinished(self) -> None:
        """Left with nothing recorded (a lone Ctrl or Win, then away): what was there stays."""
        if self.edit.keySequence().isEmpty() and self._text:
            self.set_text(self._text)

    def _finished(self) -> None:
        sequence = self.edit.keySequence()
        if sequence.isEmpty() or sequence[0].key() in _MODIFIER_KEYS:
            self.set_text(self._text)  # only the clear button (or Backspace) removes a shortcut
            return
        text = sequence.toString(QKeySequence.PortableText)
        if text != self._text:
            self._text = text
            self.clear_button.setEnabled(True)
            self.changed.emit(text)
        self._show_label()

    def _show_label(self) -> None:
        """Windows' names in the field: Win, not Meta."""
        line = self.edit.findChild(QLineEdit)
        if line is not None and self._text:
            line.setText(label(self._text))
