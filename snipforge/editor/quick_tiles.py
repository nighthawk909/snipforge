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
from .style import COLOR_NAMES, STRENGTH_KINDS, Style
from .style_sample import paint_sample

COLUMNS = 4
TOOL_NAMES = {
    "arrow": "arrows", "line": "lines", "rect": "rectangles", "ellipse": "ellipses", "pen": "the pen",
    "highlight": "the highlighter", "text": "text", "callout": "callouts", "stamp": "stamps",
    "step": "step numbers", "pixelate": "pixelate", "blur": "blur", "magnify": "magnifiers",
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


def describe(kind: str, style: Style) -> str:
    """Like "Red, 9 px, dashed": what tells one saved style from another."""
    found = saved_styles.fields_of(kind, style)
    parts = []
    if "color" in found:
        parts.append(COLOR_NAMES.get(style.color.lower(), style.color.upper()))
    if "stamp" in found:
        parts.append(style.stamp.replace("_", " "))
    if "shape" in found:
        parts.append({"rounded": "rounded box", "rect": "box", "ellipse": "ellipse", "pill": "pill"}[style.shape])
    if "width" in found:
        parts.append(f"strength {round(style.width)}" if kind in STRENGTH_KINDS else f"{round(style.width)} px")
    if "font_size" in found:
        parts.append(f"{style.font_size} pt")
    parts += [label for name, label in (("fill", "filled"), ("dash", "dashed"), ("shadow", "shadow"))
              if found.get(name)]
    return ", ".join(parts) or "Saved style"


class SavedStyleTile(_Tile):
    """One of your saved looks, drawn as its tool would draw it. Right-click or Delete removes it."""

    delete_requested = Signal()

    def __init__(self, kind: str, style: Style) -> None:
        super().__init__()
        self.kind, self.style = kind, style
        description = describe(kind, style)
        self.setToolTip(f"{description}\nRight-click, or press Delete, to remove it")
        self.setAccessibleName(f"Your quick style: {description}")

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
        if e.key() == Qt.Key_Delete:
            self.delete_requested.emit()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt API
        p = QPainter(self)
        try:
            self._paint_frame(p)
            sample = QRectF(0, 0, 40, 28)
            sample.moveCenter(QRectF(self.rect()).center())
            paint_sample(p, sample, self.kind, self.style, QColor(theme.current().palette.surface2))
        finally:
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
        self._shown: tuple[str, list[Style]] | None = None
        self.save_button = QPushButton("Save this style")
        self.save_button.setAccessibleName("Save this style as a quick style")
        self.save_button.setFocusPolicy(Qt.TabFocus)
        self.save_button.clicked.connect(self._save)
        column.addWidget(self.save_button)
        saved_styles.notifier().changed.connect(self._reload)

    @staticmethod
    def has_built_ins(kind: str) -> bool:
        return any(changes_for(kind, q) for q in QUICK_STYLES)

    def worth_showing(self, kind: str, has_selection: bool) -> bool:
        """Built-ins to click, a selection to save, or saved styles to use."""
        return self.has_built_ins(kind) or has_selection or bool(saved_styles.saved_for(kind))

    def load(self, kind: str, style: Style | None, has_selection: bool) -> None:
        self._kind, self._style, self._selected = kind, style, has_selection
        built_ins = self.has_built_ins(kind)
        for tile in self.tiles:
            tile.setVisible(built_ins)
        self._reload()

    def _reload(self) -> None:
        styles = saved_styles.saved_for(self._kind)
        if (self._kind, styles) != self._shown:  # unchanged tiles stay, and so does keyboard focus
            self._rebuild(styles)
        self._refresh_save(styles)

    def _rebuild(self, styles: list[Style]) -> None:
        focused = next((i for i, t in enumerate(self.saved_tiles) if t.hasFocus()), None)
        for tile in self.saved_tiles:
            self._mine.removeWidget(tile)
            tile.hide()
            tile.deleteLater()
        kind = self._kind
        self._shown = (kind, styles)
        self.saved_tiles = [SavedStyleTile(kind, s) for s in styles]
        previous = self.tiles[-1]
        for i, tile in enumerate(self.saved_tiles):
            tile.clicked.connect(lambda _=False, s=tile.style: self.apply.emit(saved_styles.fields_of(kind, s)))
            tile.delete_requested.connect(lambda s=tile.style: saved_styles.delete(kind, s))
            self._mine.addWidget(tile, i // COLUMNS, i % COLUMNS)
            QWidget.setTabOrder(previous, tile)  # Tab goes through them where they are: before Save
            previous = tile
        QWidget.setTabOrder(previous, self.save_button)
        self._mine_label.setVisible(bool(styles))
        if focused is not None:  # you deleted one with the keyboard: stay on the next one
            neighbour = self.saved_tiles[min(focused, len(self.saved_tiles) - 1)] if self.saved_tiles else None
            (neighbour or self.save_button).setFocus(Qt.OtherFocusReason)

    def _refresh_save(self, styles: list[Style]) -> None:
        name = TOOL_NAMES.get(self._kind, self._kind)
        if not self._selected or self._style is None:
            enabled, tip = False, f"Select markup to save its look as a quick style for {name}"
        elif any(saved_styles.same_look(self._kind, s, self._style) for s in styles):
            enabled, tip = False, "This look is one of your quick styles already"
        elif len(styles) >= saved_styles.MAX_SAVED:
            enabled, tip = False, f"You have {saved_styles.MAX_SAVED} for {name}. Delete one to save another."
        else:
            enabled, tip = True, f"Save the selected markup's look as a quick style for {name}"
        self.save_button.setEnabled(enabled)
        self.save_button.setToolTip(tip)

    def _save(self) -> None:
        if self._style is not None and saved_styles.save(self._kind, self._style):
            self.saved.emit(self._kind)
