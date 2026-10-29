"""The panel's Quick styles: the built-in looks, your saved ones for the current tool, and Save style."""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from . import saved_styles
from .items import readable_on
from .quick_styles import QUICK_STYLES, QuickStyle, changes_for
from .style import COLOR_NAMES, STRENGTH_KINDS, USUAL_CORNER, USUAL_DIM, USUAL_OPACITY, Style, usual_align
from .style_sample import paint_sample

COLUMNS = 4
REMOVE_SIZE = 16  # the × on a saved style
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
    if "heads" in found and style.heads != "end":
        parts.append("two heads" if style.heads == "both" else "head at the start")
    if found.get("open_head"):
        parts.append("open head")
    if found.get("curve"):
        parts.append("curved")
    if "shape" in found and not (kind in ("rect", "spotlight") and style.shape == "rounded"):
        parts.append(SHAPE_WORDS.get(style.shape, style.shape))
    if "corner" in found and style.corner != USUAL_CORNER:
        parts.append(f"corners {style.corner}")
    if "numbering" in found and style.numbering != "digits":
        parts.append({"letters": "A B C", "roman": "I II III"}[style.numbering])
    if "badge" in found and style.badge != "circle":
        parts.append(style.badge)
    if found.get("box"):
        parts.append("box")
    if "width" in found:
        parts.append(f"strength {round(style.width)}" if kind in STRENGTH_KINDS else f"{round(style.width)} px")
    if "font_size" in found:
        parts.append(f"{style.font_size} pt")
    if found.get("font_family"):
        parts.append(style.font_family)
    for key, word in (("italic", "italic"), ("underline", "underlined"), ("outline", "outlined"),
                      ("rounded", "rounded")):
        if found.get(key):
            parts.append(word)
    if "align" in found and style.align and style.align != usual_align(kind):
        parts.append({"left": "left aligned", "center": "centered", "right": "right aligned"}[style.align])
    if "dim" in found and style.dim != USUAL_DIM:
        parts.append(f"dim {style.dim}%")
    if found.get("fill"):
        fill = COLOR_NAMES.get(style.fill_color.lower(), style.fill_color.upper()) if style.fill_color else ""
        opacity = f"{style.fill_opacity}%" if style.fill_opacity != USUAL_OPACITY else ""
        parts.append(" ".join(["filled", fill, opacity]).strip())
    if found.get("dash"):
        parts.append("dotted" if found.get("dotted") else "dashed")
    if found.get("shadow"):
        parts.append("shadow")
    return ", ".join(parts) or "Saved style"


SHAPE_WORDS = {"rounded": "rounded box", "rect": "box", "ellipse": "ellipse", "pill": "pill", "diamond": "diamond",
               "triangle": "triangle", "star": "star"}


class SavedStyleTile(_Tile):
    """One of your saved looks, drawn as its tool would draw it. The × that shows under the mouse
    (or with keyboard focus), right-click or Delete removes it."""

    delete_requested = Signal()
    delete_all_requested = Signal()

    def __init__(self, kind: str, style: Style) -> None:
        super().__init__()
        self.kind, self.style = kind, style
        self._hovered = self._over_remove = self._pressing_remove = False
        # A tile that slides under a resting pointer (the one before it was just removed) waits for the
        # pointer to move before its × shows, so a quick second click can't remove it too.
        self._armed = False
        self.setMouseTracking(True)  # to light up the × as the pointer reaches it
        description = describe(kind, style)
        self.setToolTip(f"{description}\nClick to use it. Click ×, right-click or press Delete to remove it")
        self.setAccessibleName(f"Your quick style: {description}")

    def remove_shown(self) -> bool:
        return (self._hovered and self._armed) or self.hasFocus()

    def remove_rect(self) -> QRect:
        """The × in the top right corner."""
        return QRect(self.width() - REMOVE_SIZE - 2, 2, REMOVE_SIZE, REMOVE_SIZE)

    def _on_remove(self, pos) -> bool:
        return self.remove_shown() and self.remove_rect().contains(pos)

    def enterEvent(self, e) -> None:  # noqa: N802 - Qt API
        self._hovered = True
        self._over_remove = self._on_remove(e.position().toPoint())
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802 - Qt API
        self._hovered = self._over_remove = False
        super().leaveEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802 - Qt API
        if not self._armed:
            self._armed = True
            self.update()
        over = self._on_remove(e.position().toPoint())
        if over != self._over_remove:
            self._over_remove = over
            self.update()
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802 - Qt API
        self._pressing_remove = False  # a press whose release went missing (a menu, Alt+Tab) is over
        if e.button() == Qt.LeftButton and self._on_remove(e.position().toPoint()):
            self._pressing_remove = True  # the button never sees it, so it can't apply the style
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802 - Qt API
        if self._pressing_remove:
            self._pressing_remove = False
            e.accept()
            # Released somewhere else: you changed your mind, so nothing happens.
            if e.button() == Qt.LeftButton and self.remove_rect().contains(e.position().toPoint()):
                self.delete_requested.emit()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802 - Qt API
        if self.remove_rect().contains(e.position().toPoint()):
            e.accept()  # the second click of a double-click on × never removes (or applies) another
            return
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e) -> None:  # noqa: N802 - Qt API
        menu = QMenu(self)
        apply = menu.addAction("Apply")
        delete = menu.addAction("Delete this quick style")
        menu.addSeparator()
        delete_all = menu.addAction(f"Delete all quick styles for {TOOL_NAMES.get(self.kind, self.kind)}…")
        chosen = self._choose(menu, e.globalPos())
        if chosen is delete:
            self.delete_requested.emit()
        elif chosen is delete_all:
            self.delete_all_requested.emit()
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
            if self.remove_shown():
                self._paint_remove(p)
        finally:
            p.end()

    def _paint_remove(self, p: QPainter) -> None:
        """A small round × badge; red while the pointer is on it."""
        palette = theme.current().palette
        badge = QRectF(self.remove_rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(palette.border), 1))
        p.setBrush(QColor(palette.danger if self._over_remove else palette.surface))
        p.drawEllipse(badge)
        p.setPen(QPen(QColor("#ffffff" if self._over_remove else palette.text2), 1.5, Qt.SolidLine, Qt.RoundCap))
        inset = badge.adjusted(5, 5, -5, -5)
        p.drawLine(inset.topLeft(), inset.bottomRight())
        p.drawLine(inset.topRight(), inset.bottomLeft())


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
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self._mine_label = QLabel("SAVED")
        self._mine_label.setProperty("sfRole", "section")
        header.addWidget(self._mine_label)
        header.addStretch(1)
        self.remove_all_button = QPushButton("Remove all")
        self.remove_all_button.setProperty("variant", "ghost")
        self.remove_all_button.setFocusPolicy(Qt.TabFocus)
        self.remove_all_button.clicked.connect(self._remove_all)
        header.addWidget(self.remove_all_button)
        column.addLayout(header)
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
        QWidget.setTabOrder(self.tiles[-1], self.remove_all_button)
        previous = self.remove_all_button
        for i, tile in enumerate(self.saved_tiles):
            tile.clicked.connect(lambda _=False, s=tile.style: self.apply.emit(saved_styles.fields_of(kind, s)))
            tile.delete_requested.connect(lambda s=tile.style: saved_styles.delete(kind, s))
            tile.delete_all_requested.connect(self._remove_all)
            self._mine.addWidget(tile, i // COLUMNS, i % COLUMNS)
            QWidget.setTabOrder(previous, tile)  # Tab goes through them where they are: before Save
            previous = tile
        QWidget.setTabOrder(previous, self.save_button)
        self._mine_label.setVisible(bool(styles))
        self.remove_all_button.setVisible(bool(styles))
        count, name = len(styles), TOOL_NAMES.get(kind, kind)
        tip = f"Remove all {count} of your quick styles for {name}" if count > 1 else \
            f"Remove your quick style for {name}"
        self.remove_all_button.setToolTip(tip)
        self.remove_all_button.setAccessibleName(tip)
        if focused is not None:  # you deleted one with the keyboard: stay on the next one
            neighbour = self.saved_tiles[min(focused, len(self.saved_tiles) - 1)] if self.saved_tiles else None
            self._focus_nearby(neighbour)

    def _focus_nearby(self, first=None) -> None:
        """Keep the keyboard here: on `first`, else Save this style, else a built-in quick style."""
        target = next((w for w in (first, self.save_button, *self.tiles)
                       if w is not None and w.isEnabled() and w.isVisibleTo(self)), None)
        if target is not None:
            target.setFocus(Qt.OtherFocusReason)

    def _remove_all(self) -> None:
        kind = self._kind  # as it was when you asked: not whatever the panel shows after the dialog
        styles = saved_styles.saved_for(kind)
        if not styles:
            return
        had_focus = self.remove_all_button.hasFocus()  # before the dialog takes it
        name = TOOL_NAMES.get(kind, kind)
        what = f"all {len(styles)} of your quick styles" if len(styles) > 1 else "your quick style"
        if not self._confirm(f"Remove {what} for {name}? The built-in quick styles stay."):
            return
        saved_styles.delete_all(kind)
        if had_focus:  # the button hides with nothing left to remove: keep the keyboard nearby
            self._focus_nearby()

    def _confirm(self, text: str) -> bool:
        """Ask before removing them all (tests stand in for the person answering)."""
        answer = QMessageBox.question(self, "Remove quick styles", text, QMessageBox.Yes | QMessageBox.No,
                                      QMessageBox.No)
        return answer == QMessageBox.Yes

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
