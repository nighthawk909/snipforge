"""Pick who to send a snip to in Teams; opens that chat directly."""
from __future__ import annotations

import re
from urllib.parse import quote

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCompleter, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from ..settings import app_settings

EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
RECENT_KEY = "teams_recent"
RECENT_MAX = 15


def parse_recipients(text: str) -> tuple[list[str], list[str]]:
    """Split on commas/semicolons/spaces. Returns (valid emails, invalid entries)."""
    parts = [p.strip() for p in re.split(r"[,;\s]+", text) if p.strip()]
    valid = [p for p in parts if EMAIL.match(p)]
    return valid, [p for p in parts if not EMAIL.match(p)]


def chat_link(recipients: list[str], message: str = "") -> str:
    """Teams deep link that opens (or starts) the chat with these people in the desktop app."""
    link = "msteams:/l/chat/0/0?users=" + ",".join(quote(r) for r in recipients)
    if message:
        link += "&message=" + quote(message)
    return link


class TeamsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Send in Teams")
        self.setMinimumWidth(460)
        self._settings = app_settings()
        recent = [r for r in (self._settings.value(RECENT_KEY, []) or []) if isinstance(r, str)]

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("To (work emails, separated by commas). Several people start a group chat."))
        self.to = QLineEdit()
        self.to.setPlaceholderText("alex.kim@company.com, sam.lee@company.com")
        if recent:
            completer = QCompleter(recent, self)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            self.to.setCompleter(completer)
            self.to.setText(recent[0])
            self.to.selectAll()
        layout.addWidget(self.to)
        layout.addWidget(QLabel("Message (optional)"))
        self.message = QLineEdit()
        self.message.setPlaceholderText("Here's the screenshot")
        layout.addWidget(self.message)
        self.error = QLabel()
        self.error.setStyleSheet("color: #ef4444;")
        self.error.hide()
        layout.addWidget(self.error)
        note = QLabel("The snip is copied for you. When the chat opens, press Ctrl+V, then Enter.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #8a8a8a;")
        layout.addWidget(note)

        buttons = QHBoxLayout()
        self.just_open = QPushButton("Just open Teams")
        self.just_open.clicked.connect(lambda: self.done(2))
        buttons.addWidget(self.just_open)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        send = QPushButton("Open chat")
        send.setDefault(True)
        send.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(send)
        layout.addLayout(buttons)
        self.to.textChanged.connect(lambda _t: self.error.hide())
        self.recipients: list[str] = []

    def _accept(self) -> None:
        valid, invalid = parse_recipients(self.to.text())
        if not valid:
            self._show_error("Enter at least one work email.")
            return
        if invalid:
            self._show_error(f"These don't look like emails: {', '.join(invalid)}")
            return
        self.recipients = valid
        self._remember(", ".join(valid))
        self.accept()

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.show()

    def _remember(self, entry: str) -> None:
        recent = [r for r in (self._settings.value(RECENT_KEY, []) or []) if isinstance(r, str) and r != entry]
        self._settings.setValue(RECENT_KEY, [entry] + recent[:RECENT_MAX - 1])
