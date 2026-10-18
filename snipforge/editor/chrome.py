"""The editor's command bar (top) and recent-snips tray (bottom)."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QToolButton,
    QWidget,
)

from .. import theme
from ..icons import icon
from ..widgets import ToggleSwitch
from ..wording import keys, redo_keys

ZOOM_STEP = 1.25
RECENT_COUNT = 10
COMPACT_BELOW = 1180  # command bar width where secondary buttons drop their labels


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
    edit_text = Signal()
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
        self._labels: dict[QPushButton, str] = {}  # buttons that drop their label when the bar is narrow
        self._compact = False

        self.new_button = self._button("New capture", "plus", f"Take a new snip ({keys('Ctrl+N')})", self.new_capture,
                                       variant="outline", keep_label=True)
        row.addWidget(self.new_button)
        row.addSpacing(4)
        row.addWidget(_divider())
        row.addSpacing(4)
        self.undo_button = self._tool("undo", f"Undo ({keys('Ctrl+Z')})", self.undo)
        self.redo_button = self._tool("redo", f"Redo ({redo_keys()})", self.redo)
        row.addWidget(self.undo_button)
        row.addWidget(self.redo_button)
        row.addStretch(1)
        row.addWidget(self._tool("zoom_out", f"Zoom out ({keys('Ctrl+wheel')})", self.zoom_out))
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("ZoomLabel")
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label.setMinimumWidth(48)
        row.addWidget(self.zoom_label)
        row.addWidget(self._tool("zoom_in", f"Zoom in ({keys('Ctrl+wheel')})", self.zoom_in))
        row.addWidget(self._button("Fit", None, f"Fit the snip in the window ({keys('Ctrl+0')})", self.fit,
                                   keep_label=True))
        row.addStretch(1)
        row.addWidget(self._button("Grab text", "ocr", f"Copy the text in this snip ({keys('Ctrl+Shift+T')})",
                                   self.grab_text))
        self.edit_text_button = self._button("Edit text", "text", "Retype a line of text in the snip: it's "
                                             f"replaced in a matching size and colour ({keys('Ctrl+Shift+E')})",
                                             self.edit_text)
        row.addWidget(self.edit_text_button)
        row.addWidget(self._button("Redact", "redact", "Blur emails, phone and card numbers automatically",
                                   self.redact))
        row.addWidget(self._button("Pin", "pin", f"Float this snip on top of other windows ({keys('Ctrl+P')})",
                                   self.pin))
        self.share_button = self._button("Share", "share", "Email, Teams, OneDrive link and more", None)
        row.addWidget(self.share_button)
        row.addWidget(self._button("Save", "save", "Save to your Save folder, as PNG, JPG, WebP or PDF (set in "
                                   f"Settings). {keys('Ctrl+S')}; {keys('Ctrl+Shift+S')} to choose where", self.save))
        row.addSpacing(4)
        self.copy_button = self._button("Copy", "copy", f"Copy to clipboard ({keys('Ctrl+C')})", self.copy,
                                        variant="primary", keep_label=True)
        row.addWidget(self.copy_button)
        self.theme_button = self._tool("moon", "Switch between light and dark", self.toggle_theme)
        row.addWidget(self.theme_button)
        for w in self.findChildren(QWidget):  # reachable with Tab; clicks leave the keyboard on the canvas
            w.setFocusPolicy(Qt.TabFocus if isinstance(w, (QPushButton, QToolButton)) else Qt.NoFocus)
        self.retheme()

    def _button(self, text: str, icon_name: str | None, tip: str, signal, variant: str | None = None,
                keep_label: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setToolTip(tip)
        b.setAccessibleName(text)
        if not keep_label:
            self._labels[b] = text
            b.setMinimumWidth(36)  # may shrink to its icon, so the bar never forces a wide window
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
        widget.setFocusPolicy(Qt.TabFocus)
        widget.setAccessibleName(widget.text())
        self._icons.append((widget, icon_name, ""))
        self._labels[widget] = widget.text()
        widget.setMinimumWidth(36)
        self._apply_compact(self._compact)
        self.retheme()

    def resizeEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(e)
        compact = self.width() < COMPACT_BELOW
        if compact != self._compact:
            self._apply_compact(compact)

    def _apply_compact(self, compact: bool) -> None:
        self._compact = compact
        for button, label in self._labels.items():
            button.setText("" if compact else label)

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
        tip = "Switch to light" if t.dark else "Switch to dark"
        self.theme_button.setToolTip(tip)
        self.theme_button.setAccessibleName(tip)


class Tray(QWidget):
    """Recent snips on the left; status messages, Beautify, image size and save state on the right."""

    open_entry = Signal(str)
    open_library = Signal()
    beautify_changed = Signal(bool)

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
        strip = QWidget()
        self._thumbs = QHBoxLayout(strip)
        self._thumbs.setContentsMargins(0, 0, 0, 0)
        self._thumbs.setSpacing(6)
        self._thumbs.addStretch(1)
        self._strip = QScrollArea()  # recent snips shrink away before messages do
        self._strip.setObjectName("TrayThumbs")
        self._strip.setWidget(strip)
        self._strip.setWidgetResizable(True)
        self._strip.setFrameShape(QFrame.NoFrame)
        self._strip.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._strip.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._strip.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._strip.setFixedHeight(46)
        row.addWidget(self._strip, 2)
        self.library_button = QPushButton("Open library")
        self.library_button.setProperty("variant", "ghost")
        self.library_button.clicked.connect(self.open_library)
        row.addWidget(self.library_button)
        self.status = QStatusBar()
        self.status.setSizeGripEnabled(False)
        self.status.setObjectName("TrayStatus")
        self.status.setMinimumWidth(240)
        row.addWidget(self.status, 3)
        beautify_label = QLabel("Beautify")
        beautify_label.setToolTip("A backdrop, rounded corners and a shadow, shown around your snip here and added "
                                  "when you copy, save, drag, pin or share")
        row.addWidget(beautify_label)
        self.beautify = ToggleSwitch("Beautify when copying, saving or dragging")
        self.beautify.toggled.connect(lambda on: None if self._loading else self.beautify_changed.emit(on))
        row.addWidget(self.beautify)
        row.addSpacing(6)
        self._size = QLabel()
        self._size.setProperty("sfRole", "muted")
        row.addWidget(self._size)
        self._saved = QLabel()
        self._saved.setProperty("sfRole", "muted")
        row.addWidget(self._saved)
        self._loading = False
        for w in self.findChildren(QWidget):
            w.setFocusPolicy(Qt.TabFocus if isinstance(w, (QPushButton, QToolButton, ToggleSwitch)) else Qt.NoFocus)

    def set_recent(self, entries) -> None:
        while self._thumbs.count() > 1:  # keep the trailing stretch
            widget = self._thumbs.takeAt(0).widget()
            if widget:
                widget.hide()
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
            thumb.setFocusPolicy(Qt.TabFocus)
            thumb.clicked.connect(lambda _=False, i=entry.id: self.open_entry.emit(i))
            self._thumbs.insertWidget(self._thumbs.count() - 1, thumb)

    def thumb_count(self) -> int:
        return self._thumbs.count() - 1

    def set_beautify(self, on: bool) -> None:
        self._loading = True
        self.beautify.setChecked(on)
        self._loading = False

    def set_image_size(self, width: int, height: int) -> None:
        self._size.setText(f"{width} × {height} px")

    def set_saved(self, text: str, tip: str = "") -> None:
        self._saved.setText(text)
        self._saved.setToolTip(tip)

    def saved_text(self) -> str:
        return self._saved.text()
