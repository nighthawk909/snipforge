"""Dialog showing text grabbed from a snip."""
from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from ..fonts import mono_font


class GrabbedTextDialog(QDialog):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Grabbed text")
        self.resize(520, 360)
        layout = QVBoxLayout(self)
        note = QLabel("Copied to clipboard. Edit below and copy again if needed."
                      if text else "No text was found in this snip.")
        layout.addWidget(note)
        self._edit = QPlainTextEdit(text)
        self._edit.setFont(mono_font(10))
        layout.addWidget(self._edit, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._edit.toPlainText()))
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        close.setDefault(True)
        buttons.addWidget(copy)
        buttons.addWidget(close)
        layout.addLayout(buttons)
