"""The panel's Quick styles: the built-in looks, your saved ones for the current tool, and Save style."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QGridLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from . import saved_styles
from .items import readable_on
from .quick_styles import QUICK_STYLES, QuickStyle, changes_for
from .style import Style
from .style_sample import paint_sample

COLUMNS = 4
TOOL_NAMES = {
    "arrow": "arrows", "line": "lines", "rect": "rectangles", "ellipse": "ellipses", "pen": "the pen",
    "highlight": "the highlighter", "text": "text", "callout": "callouts", "stamp": "stamps",
    "step": "step numbers", "pixelate": "pixelate", "blur": "blur",
}


class _Tile(QAbstractButton):
    """A rounded tile that highlights under the mouse."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(44, 40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(56, 40)

    def _paint_frame(self, p: QPainter) -> None:
        t = theme.current()
        p.setRenderHint(QPainter.Antialiasing)
        edge = t.accent.fill if self.hasFocus() else (t.palette.text3 if self.underMouse() else t.palette.border)
        p.setPen(QPen(QColor(edge), 1))
        p.setBrush(QColor(t.palette.surface2))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)

    def enterEvent(self, e) -> None:  # noqa: N802 - Qt API
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802 - Qt API
        self.update()
        super().leaveEvent(e)


class QuickStyleTile(_Tile):
    """One of the built-in looks: a color, filled or outlined."""

    def __init__(self, quick: QuickStyle) -> None:
        super().__init__()
        self.quick = quick
        self.setToolTip(quick.label)
        self.setAccessibleName(f"Quick style: {quick.label}")

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        self._paint_frame(p)
        color = QColor(self.quick.color)
        sample = QRectF(0, 0, 36, 22)
        sample.moveCenter(QRectF(self.rect()).center())
        p.setPen(QPen(color if self.quick.color.lower() != "#ffffff" else QColor("#1c1c1e"), 2))
        p.setBrush(color if self.quick.fill_on else QColor("#ffffff"))
        p.drawRoundedRect(sample, 6, 6)
        p.setPen(readable_on(color) if self.quick.fill_on else QColor("#1c1c1e"))
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(8)
        p.setFont(font)
        p.drawText(sample, Qt.AlignCenter, "Aa")
        p.end()


class SavedStyleTile(_Tile):
    """One of your saved looks, drawn as its tool would draw it. Right-click or Delete removes it."""

    delete_requested = Signal()

    def __init__(self, kind: str, style: Style) -> None:
        super().__init__()
        self.kind, self.style = kind, style
        self.setToolTip("Your quick style. Right-click to delete it.")
        self.setAccessibleName(f"Saved quick style {style.color}")

    def contextMenuEvent(self, e) -> None:  # noqa: N802 - Qt API
        menu = QMenu(self)
        apply = menu.addAction("Apply")
        delete = menu.addAction("Delete this quick style")
        chosen = self._choose(menu, e.globalPos())
        if chosen is delete:
            self.delete_requested.emit()
        elif chosen is apply:
            self.click()

    @staticmethod
    def _choose(menu: QMenu, pos):
        """Show the menu and return the chosen action (tests stand in for the person choosing)."""
        return menu.exec(pos)

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt API
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_requested.emit()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        self._paint_frame(p)
        sample = QRectF(0, 0, 40, 28)
        sample.moveCenter(QRectF(self.rect()).center())
        paint_sample(p, sample, self.kind, self.style)
        p.end()


class QuickStyles(QWidget):
    """Goes in the panel's Quick styles section."""

    apply = Signal(dict)  # style field -> value, for the selection or the tool
    saved = Signal(str)  # the tool a style was saved for

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._kind, self._style, self._selected = "select", None, False
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        built_in = QGridLayout()
        built_in.setSpacing(8)
        self.tiles = [QuickStyleTile(q) for q in QUICK_STYLES]
        for i, tile in enumerate(self.tiles):
            tile.clicked.connect(lambda _=False, q=tile.quick: self.apply.emit(changes_for(self._kind, q)))
            built_in.addWidget(tile, i // COLUMNS, i % COLUMNS)
        column.addLayout(built_in)
        self._mine_label = QLabel("SAVED")
        self._mine_label.setProperty("sfRole", "section")
        column.addWidget(self._mine_label)
        self._mine = QGridLayout()
        self._mine.setSpacing(8)
        for col in range(COLUMNS):  # the same width as the built-in tiles, however many you've saved
            built_in.setColumnStretch(col, 1)
            self._mine.setColumnStretch(col, 1)
        column.addLayout(self._mine)
        self.saved_tiles: list[SavedStyleTile] = []
        self.save_button = QPushButton("Save this style")
        self.save_button.setAccessibleName("Save this style as a quick style")
        self.save_button.setFocusPolicy(Qt.TabFocus)
        self.save_button.clicked.connect(self._save)
        column.addWidget(self.save_button)
        saved_styles.notifier().changed.connect(self._reload)

    def load(self, kind: str, style: Style | None, has_selection: bool) -> None:
        self._kind, self._style, self._selected = kind, style, has_selection
        built_ins = any(changes_for(kind, q) for q in QUICK_STYLES)
        for tile in self.tiles:
            tile.setVisible(built_ins)
        self._reload()

    def _reload(self) -> None:
        for tile in self.saved_tiles:
            self._mine.removeWidget(tile)
            tile.hide()
            tile.deleteLater()
        styles = saved_styles.saved_for(self._kind)
        self.saved_tiles = [SavedStyleTile(self._kind, s) for s in styles]
        for i, tile in enumerate(self.saved_tiles):
            tile.clicked.connect(lambda _=False, s=tile.style: self.apply.emit(saved_styles.fields_of(self._kind, s)))
            tile.delete_requested.connect(lambda i=i: saved_styles.delete(self._kind, i))
            self._mine.addWidget(tile, i // COLUMNS, i % COLUMNS)
        self._mine_label.setVisible(bool(styles))
        self._refresh_save(styles)

    def _refresh_save(self, styles: list[Style]) -> None:
        name = TOOL_NAMES.get(self._kind, self._kind)
        if not self._selected or self._style is None:
            enabled, tip = False, f"Select markup to save its look as a quick style for {name}"
        elif any(saved_styles.fields_of(self._kind, s) == saved_styles.fields_of(self._kind, self._style)
                 for s in styles):
            enabled, tip = False, "This look is one of your quick styles already"
        elif len(styles) >= saved_styles.MAX_SAVED:
            enabled, tip = False, f"You have {saved_styles.MAX_SAVED} for {name}. Right-click one to delete it."
        else:
            enabled, tip = True, f"Save the selected markup's look as a quick style for {name}"
        self.save_button.setEnabled(enabled)
        self.save_button.setToolTip(tip)

    def _save(self) -> None:
        if self._style is not None and saved_styles.save(self._kind, self._style):
            self.saved.emit(self._kind)
