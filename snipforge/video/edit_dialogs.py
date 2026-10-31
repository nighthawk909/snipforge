"""The video editor's title and caption windows. ask_title and ask_caption return ("set", value),
("remove", None) or None for Cancel; the editor's tests replace them."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..widgets import screen_reader_name
from .edit import MAX_TEXT, MAX_TITLE, MIN_TITLE, Title, title_card

PREVIEW_WIDTH = 360


class _EditDialog(QDialog):
    """OK, Cancel and (when there's one to remove) Remove."""

    def __init__(self, heading: str, removable: bool, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle(heading)
        self.removed = False
        self.column = QVBoxLayout(self)
        self.form = QFormLayout()
        self.column.addLayout(self.form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_button = self.buttons.button(QDialogButtonBox.Ok)
        self.remove_button = QPushButton("Remove")
        self.buttons.addButton(self.remove_button, QDialogButtonBox.DestructiveRole)
        self.remove_button.setVisible(removable)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.remove_button.clicked.connect(self._remove)

    def finish_layout(self) -> None:
        self.column.addWidget(self.buttons)

    def _remove(self) -> None:
        self.removed = True
        self.accept()


class TitleDialog(_EditDialog):
    """A title card's words and how long it's shown, with the card as it will look."""

    def __init__(self, current: Title | None, size: QSize, parent: QWidget | None = None) -> None:
        super().__init__("Title", current is not None, parent)
        self._size = size
        self.text = QLineEdit(current.text if current else "")
        self.text.setMaxLength(MAX_TEXT)
        self.text.setPlaceholderText("What the video is about")
        screen_reader_name(self.text, "Title")
        self.seconds = QDoubleSpinBox()
        self.seconds.setRange(MIN_TITLE, MAX_TITLE)
        self.seconds.setSingleStep(0.5)
        self.seconds.setDecimals(1)
        self.seconds.setSuffix(" s")
        self.seconds.setValue(current.seconds if current else 3.0)
        screen_reader_name(self.seconds, "Shown for, in seconds")
        self.form.addRow("Title", self.text)
        self.form.addRow("Shown for", self.seconds)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setAccessibleName("How the title card looks")
        self.column.addWidget(self.preview)
        note = QLabel("It's shown before the video, with silence.")
        note.setProperty("sfRole", "muted")
        self.column.addWidget(note)
        self.finish_layout()
        self.text.textChanged.connect(self._changed)
        self._changed()

    def result_title(self) -> Title | None:
        try:
            return Title(self.text.text(), self.seconds.value())
        except ValueError:
            return None

    def _changed(self) -> None:
        title = self.result_title()
        self.ok_button.setEnabled(title is not None)
        shown = title or Title("Your title", 3.0)
        card = title_card(shown, self._size)
        self.preview.setPixmap(QPixmap.fromImage(card).scaledToWidth(PREVIEW_WIDTH, Qt.SmoothTransformation))


class CaptionDialog(_EditDialog):
    """Words along the bottom of the whole video."""

    def __init__(self, current: str, parent: QWidget | None = None) -> None:
        super().__init__("Caption", bool(current), parent)
        self.text = QLineEdit(current)
        self.text.setMaxLength(MAX_TEXT)
        self.text.setPlaceholderText("Shown along the bottom of the video")
        screen_reader_name(self.text, "Caption")
        self.form.addRow("Caption", self.text)
        self.finish_layout()
        self.text.textChanged.connect(lambda text: self.ok_button.setEnabled(bool(text.strip())))
        self.ok_button.setEnabled(bool(current.strip()))


def ask_title(parent: QWidget | None, current: Title | None, size: QSize):
    dialog = TitleDialog(current, size, parent)
    if dialog.exec() != QDialog.Accepted:
        return None
    return ("remove", None) if dialog.removed else ("set", dialog.result_title())


def ask_caption(parent: QWidget | None, current: str):
    dialog = CaptionDialog(current, parent)
    if dialog.exec() != QDialog.Accepted:
        return None
    return ("remove", None) if dialog.removed else ("set", dialog.text.text())
