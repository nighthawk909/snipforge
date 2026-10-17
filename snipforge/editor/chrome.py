"""The editor's command bar (top) and recent-snips tray (bottom)."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStatusBar, QToolButton, QWidget

from .. import theme
from ..icons import icon

ZOOM_STEP = 1.25
RECENT_COUNT = 10


def _divider() -> QWidget:
    line = QWidget()
    line.setObjectName("Divider")
    line.setAttribute(Qt.WA_StyledBackground, True)
    line.setFixedSize(1, 20)
    return line


class CommandBar(QWidget):
    """New capture, undo/redo, zoom, the snip actions and Copy. Follows the theme's header band."""

    new_capture = Signal()
    undo = Signal()
    redo = Signal()
    zoom_out = Signal()
    zoom_in = Signal()
    fit = Signal()
    grab_text = Signal()
    redact = Signal()
    pin = Signal()
    save = Signal()
    copy = Signal()
    toggle_theme = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CommandBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(4)
        self._icons: list[tuple[QPushButton | QToolButton, str, str]] = []  # widget, icon, role

        self.new_button = self._button("New capture", "plus", "Take a new snip (Ctrl+N)", self.new_capture,
                                       variant="outline")
        row.addWidget(self.new_button)
        row.addSpacing(4)
        row.addWidget(_divider())
        row.addSpacing(4)
        self.undo_button = self._tool("undo", "Undo (Ctrl+Z)", self.undo)
        self.redo_button = self._tool("redo", "Redo (Ctrl+Y)", self.redo)
        row.addWidget(self.undo_button)
        row.addWidget(self.redo_button)
        row.addStretch(1)
        row.addWidget(self._tool("zoom_out", "Zoom out (Ctrl+wheel)", self.zoom_out))
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("ZoomLabel")
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label.setMinimumWidth(48)
        row.addWidget(self.zoom_label)
        row.addWidget(self._tool("zoom_in", "Zoom in (Ctrl+wheel)", self.zoom_in))
        row.addWidget(self._button("Fit", None, "Fit the snip in the window (Ctrl+0)", self.fit))
        row.addStretch(1)
        row.addWidget(self._button("Grab text", "ocr", "Copy the text in this snip (Ctrl+Shift+T)", self.grab_text))
        row.addWidget(self._button("Redact", "redact", "Blur emails, phone and card numbers automatically",
                                   self.redact))
        row.addWidget(self._button("Pin", "pin", "Float this snip on top of other windows (Ctrl+P)", self.pin))
        self.share_button = self._button("Share", "share", "Email, Teams, OneDrive link and more", None)
        row.addWidget(self.share_button)
        row.addWidget(self._button("Save", "save", "Save a PNG to Pictures\\SnipForge (Ctrl+S)", self.save))
        row.addSpacing(4)
        self.copy_button = self._button("Copy", "copy", "Copy to clipboard (Ctrl+C)", self.copy, variant="primary")
        row.addWidget(self.copy_button)
        self.theme_button = self._tool("moon", "Switch between light and dark", self.toggle_theme)
        row.addWidget(self.theme_button)
        for w in self.findChildren(QWidget):  # keep keyboard focus on the canvas
            w.setFocusPolicy(Qt.NoFocus)
        self.retheme()

    def _button(self, text: str, icon_name: str | None, tip: str, signal, variant: str | None = None) -> QPushButton:
        b = QPushButton(text)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        if variant:
            b.setProperty("variant", variant)
        if signal is not None:
            b.clicked.connect(signal)
        if icon_name:
            self._icons.append((b, icon_name, variant or ""))
        return b

    def _tool(self, icon_name: str, tip: str, signal) -> QToolButton:
        b = QToolButton()
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setIconSize(QSize(18, 18))
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(signal)
        self._icons.append((b, icon_name, ""))
        return b

    def insert_before_share(self, widget: QPushButton, icon_name: str) -> None:
        """For buttons the window owns, like Drag out."""
        row = self.layout()
        row.insertWidget(row.indexOf(self.share_button), widget)
        widget.setFocusPolicy(Qt.NoFocus)
        self._icons.append((widget, icon_name, ""))
        self.retheme()

    def set_zoom(self, zoom: float) -> None:
        self.zoom_label.setText(f"{round(zoom * 100)}%")

    def set_history(self, can_undo: bool, can_redo: bool) -> None:
        self.undo_button.setEnabled(can_undo)
        self.redo_button.setEnabled(can_redo)

    def retheme(self, _theme=None) -> None:
        t = theme.current()
        header = t.header
        for widget, name, role in self._icons:
            if widget is self.theme_button:
                name = "sun" if t.dark else "moon"
            color = header.primary_text if role == "primary" else header.ink
            widget.setIcon(icon(name, color, color, color))
        self.theme_button.setToolTip("Switch to light" if t.dark else "Switch to dark")


class Tray(QWidget):
    """Recent snips on the left; status messages, image size and save state on the right."""

    open_entry = Signal(str)
    open_library = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Tray")
        self.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(6)
        self._label = QLabel("Recent")
        self._label.setProperty("sfRole", "section")
        row.addWidget(self._label)
        self._thumbs = QHBoxLayout()
        self._thumbs.setSpacing(6)
        row.addLayout(self._thumbs)
        self.library_button = QPushButton("Open library")
        self.library_button.setProperty("variant", "ghost")
        self.library_button.clicked.connect(self.open_library)
        row.addWidget(self.library_button)
        self.status = QStatusBar()
        self.status.setSizeGripEnabled(False)
        self.status.setObjectName("TrayStatus")
        row.addWidget(self.status, 1)
        self._size = QLabel()
        self._size.setProperty("sfRole", "muted")
        row.addWidget(self._size)
        self._saved = QLabel()
        self._saved.setProperty("sfRole", "muted")
        row.addWidget(self._saved)
        for w in self.findChildren(QWidget):
            w.setFocusPolicy(Qt.NoFocus)

    def set_recent(self, entries) -> None:
        while self._thumbs.count():
            widget = self._thumbs.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        self._label.setVisible(bool(entries))
        for entry in entries[:RECENT_COUNT]:
            thumb = QToolButton()
            thumb.setObjectName("Thumb")
            pix = QPixmap(str(entry.preview_path))
            if not pix.isNull():
                thumb.setIcon(QIcon(pix.scaled(128, 76, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            thumb.setIconSize(QSize(56, 34))
            when = entry.created.strftime("%b %d, %I:%M %p")
            thumb.setToolTip(f"{when} · click to open")
            thumb.setAccessibleName(f"Open snip from {when}")
            thumb.setFocusPolicy(Qt.NoFocus)
            thumb.clicked.connect(lambda _=False, i=entry.id: self.open_entry.emit(i))
            self._thumbs.addWidget(thumb)

    def thumb_count(self) -> int:
        return self._thumbs.count()

    def set_image_size(self, width: int, height: int) -> None:
        self._size.setText(f"{width} × {height} px")

    def set_saved(self, text: str) -> None:
        self._saved.setText(text)
