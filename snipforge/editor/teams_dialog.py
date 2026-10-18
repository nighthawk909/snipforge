"""Send in Teams: the share window with the snip attached (you pick Teams and the person there), or,
with a work or school account, a chat opened straight from email addresses."""
from __future__ import annotations

import re
from urllib.parse import quote

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ..settings import app_settings
from ..wording import keys

EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
RECENT_KEY = "teams_recent"
RECENT_MAX = 15
SHARE, CHAT, OPEN = "share", "chat", "open"  # TeamsDialog.choice


def parse_recipients(text: str) -> tuple[list[str], list[str]]:
    """Split on commas/semicolons/spaces. Returns (valid emails, invalid entries)."""
    parts = [p.strip() for p in re.split(r"[,;\s]+", text) if p.strip()]
    valid = [p for p in parts if EMAIL.match(p)]
    return valid, [p for p in parts if not EMAIL.match(p)]


def chat_link(recipients: list[str], message: str = "") -> str:
    """Teams deep link that opens (or starts) the chat with these people in the desktop app. Teams fills
    in the people only for work or school addresses, and may leave out the message."""
    link = "msteams:/l/chat/0/0?users=" + ",".join(quote(r) for r in recipients)
    if message:
        link += "&message=" + quote(message)
    return link


class TeamsDialog(QDialog):
    """`choice` says how to send once it's accepted: SHARE (the share window), CHAT (a chat with
    `recipients`) or OPEN (just open Teams). `can_share` is False where there's no share window."""

    def __init__(self, parent=None, can_share: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("Send in Teams")
        self.setMinimumWidth(460)
        self._settings = app_settings()
        self.choice = ""
        self.recipients: list[str] = []
        recent = [r for r in (self._settings.value(RECENT_KEY, []) or []) if isinstance(r, str)]

        layout = QVBoxLayout(self)
        self.message = self._field("Message (optional)", "Here's the screenshot", "Message", layout)

        # Every widget gets the dialog as its parent at once: a parentless one shown even briefly is a window.
        self.share_button = QPushButton("Share to Teams…", self)
        if can_share:
            layout.addWidget(self._note(
                "Opens Windows' share window with the snip attached: pick Teams, then who to send it to. "
                "If you write a message, it's copied so you can paste it into Teams' message box."))
            self.share_button.setProperty("variant", "primary")
            self.share_button.clicked.connect(self._share)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(self.share_button)
            layout.addLayout(row)
            line = QFrame(self)
            line.setFrameShape(QFrame.HLine)
            layout.addWidget(line)
            to_label = "Or open a chat by email, with a work or school account. Several people start a group chat."
        else:
            self.share_button.hide()
            to_label = "To (work emails, separated by commas). Several people start a group chat."
        self.to = self._field(to_label, "alex.kim@company.com, sam.lee@company.com", "Recipient emails", layout)
        if recent:
            completer = QCompleter(recent, self)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            self.to.setCompleter(completer)
            self.to.setText(recent[0])
            self.to.selectAll()
        self.error = QLabel(self)
        self.error.setProperty("sfRole", "error")
        self.error.hide()
        layout.addWidget(self.error)
        layout.addWidget(self._note(f"The snip is copied for you. When the chat opens, press {keys('Ctrl+V')}, "
                                    f"then {keys('Enter')}."))

        buttons = QHBoxLayout()
        self.just_open = QPushButton("Just open Teams", self)
        self.just_open.clicked.connect(lambda: self._done_with(OPEN))
        if can_share:
            self.just_open.hide()  # the share window does this better where there is one
        buttons.addWidget(self.just_open)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        self.chat_button = QPushButton("Open chat", self)
        self.chat_button.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.chat_button)
        layout.addLayout(buttons)
        # No dialog-wide default button: Enter does what the field you're in is for, or presses the
        # button you've tabbed to (keyPressEvent).
        for button in (self.share_button, self.just_open, cancel, self.chat_button):
            button.setAutoDefault(False)
            button.setDefault(False)
        self.message.returnPressed.connect(self._share if can_share else self._accept)
        self.to.returnPressed.connect(self._accept)
        self.to.textChanged.connect(lambda _t: self.error.hide())
        (self.message if can_share else self.to).setFocus()  # without a share window, who to send to comes first

    def _field(self, label: str, placeholder: str, name: str, layout: QVBoxLayout) -> QLineEdit:
        caption = QLabel(label, self)
        caption.setWordWrap(True)
        field = QLineEdit(self)
        field.setPlaceholderText(placeholder)
        field.setAccessibleName(name)
        caption.setBuddy(field)
        layout.addWidget(caption)
        layout.addWidget(field)
        return field

    def _note(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setWordWrap(True)
        label.setProperty("sfRole", "muted")
        return label

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt API
        focused = self.focusWidget()
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and isinstance(focused, QPushButton):
            focused.click()  # without auto-default, Qt's buttons only answer Space
            return
        super().keyPressEvent(e)

    def _share(self) -> None:
        self._done_with(SHARE)

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
        self._done_with(CHAT)

    def _done_with(self, choice: str) -> None:
        self.choice = choice
        self.accept()

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.show()
        self.to.setFocus()

    def _remember(self, entry: str) -> None:
        recent = [r for r in (self._settings.value(RECENT_KEY, []) or []) if isinstance(r, str) and r != entry]
        self._settings.setValue(RECENT_KEY, [entry] + recent[:RECENT_MAX - 1])
