"""Customize tools: arrange the editor's tool rail.

A list of every tool in the rail's order, with the dividers between groups. Drag a tool (or use
Move up and Move down) to put it where you want, untick it to hide it (its key still works, and
Ctrl+K still finds it), star it to show it in Favorites at the top, and add or remove dividers to
regroup. Labels and the rail's side are here too. Every change applies as you make it, to every open
editor, and Reset puts everything back.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..icons import icon
from ..widgets import ToggleSwitch
from ..wording import keys
from . import palette as palette_store
from .palette import TOOL_NAMES, Palette, key_of, short_title, title_of

DIVIDER = "—"  # a divider row's data; tools carry their name
STAR = "★ "


class CustomizeToolsDialog(QDialog):
    changed = Signal(object)  # the Palette, every time something here changes it

    def __init__(self, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Customize tools")
        self.setObjectName("CustomizeTools")
        self.setMinimumSize(QSize(660, 560))
        self._palette = palette
        self._filling = False

        root = QHBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(8)
        title = QLabel("Tools")
        title.setProperty("sfRole", "heading")
        left.addWidget(title)
        hint = QLabel("Drag a tool to move it, untick it to take it off the rail (its key still works), "
                      f"and star it to show it in Favorites at the top. {keys('Ctrl+K')} finds any tool.")
        hint.setWordWrap(True)
        hint.setProperty("sfRole", "secondary")
        left.addWidget(hint)
        self._list = QListWidget()
        self._list.setObjectName("ToolList")
        self._list.setDragDropMode(QAbstractItemView.InternalMove)
        self._list.setDefaultDropAction(Qt.MoveAction)
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.setIconSize(QSize(20, 20))
        self._list.setAccessibleName("Tools, in the rail's order")
        self._list.model().rowsMoved.connect(lambda *_: self._read_back())
        self._list.itemChanged.connect(lambda _item: self._read_back())
        self._list.currentRowChanged.connect(lambda _row: self._refresh_buttons())
        left.addWidget(self._list, 1)
        row = QHBoxLayout()
        row.setSpacing(6)
        self._up = self._button(row, "Move up", f"Move the tool up ({keys('Alt+Up')})", lambda: self._nudge(-1))
        self._down = self._button(row, "Move down", f"Move the tool down ({keys('Alt+Down')})", lambda: self._nudge(1))
        self._star = self._button(row, "Favorite", "Show it in Favorites at the top of the rail, too",
                                  self._toggle_favorite)
        self._divider = self._button(row, "Add divider", "Start a new group above this tool", self._toggle_divider)
        row.addStretch(1)
        left.addLayout(row)
        root.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(10)
        rail = QLabel("Rail")
        rail.setProperty("sfRole", "heading")
        right.addWidget(rail)
        self._labels = self._switch(right, "Show labels", "Each tool's name beside its icon, when the window is "
                                    "tall enough for them")
        self._labels.toggled.connect(lambda on: self._update(self._palette.with_changes(labels=on)))
        self._right = self._switch(right, "Tools on the right", "Next to the properties panel")
        self._right.toggled.connect(lambda on: self._update(self._palette.with_changes(side="right" if on else "left")))
        right.addStretch(1)
        self._reset = QPushButton("Reset to default")
        self._reset.setToolTip("Every tool back in its usual place, no favorites, no labels, tools on the left")
        self._reset.clicked.connect(lambda: self._update(Palette()))
        right.addWidget(self._reset)
        done = QPushButton("Done")
        done.setProperty("variant", "primary")
        done.setDefault(True)
        done.clicked.connect(self.accept)
        right.addWidget(done)
        root.addLayout(right)

        self._fill()
        palette_store.notifier().changed.connect(self._follow)

    # ------------------------------------------------------------------ building
    @staticmethod
    def _button(row: QHBoxLayout, text: str, tip: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setToolTip(tip)
        b.clicked.connect(slot)
        row.addWidget(b)
        return b

    @staticmethod
    def _switch(col: QVBoxLayout, label: str, description: str) -> ToggleSwitch:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        text = QLabel(label)
        row.addWidget(text, 1)
        switch = ToggleSwitch(label)
        switch.setToolTip(description)
        row.addWidget(switch)
        col.addWidget(box)
        note = QLabel(description)
        note.setWordWrap(True)
        note.setProperty("sfRole", "secondary")
        note.setMaximumWidth(200)
        col.addWidget(note)
        return switch

    # ------------------------------------------------------------------ the list
    def _fill(self) -> None:
        """The list from the palette: every group's tools, a divider between groups."""
        self._filling = True
        current = self._current_name()
        self._list.clear()
        for n, group in enumerate(self._palette.groups):
            if n:
                self._add_divider()
            for name in group:
                self._add_tool(name)
        self._labels.setChecked(self._palette.labels)
        self._right.setChecked(self._palette.side == "right")
        self._reset.setEnabled(not self._palette.is_default())
        self._filling = False
        self._select(current)
        self._refresh_buttons()

    def _add_tool(self, name: str) -> None:
        item = QListWidgetItem(icon(name), self._label_for(name))
        item.setData(Qt.UserRole, name)
        item.setToolTip(f"{title_of(name)} ({key_of(name)})")
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
        item.setCheckState(Qt.Unchecked if name in self._palette.hidden else Qt.Checked)
        self._list.addItem(item)

    def _add_divider(self) -> None:
        item = QListWidgetItem("———  divider  ———")
        item.setData(Qt.UserRole, DIVIDER)
        item.setTextAlignment(Qt.AlignCenter)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
        font = QFont(self._list.font())
        font.setPointSizeF(max(6.0, font.pointSizeF() - 2))
        item.setFont(font)
        item.setForeground(self._list.palette().placeholderText())
        self._list.addItem(item)

    def _label_for(self, name: str) -> str:
        star = STAR if name in self._palette.favorites else ""
        return f"{star}{short_title(name)}  ({key_of(name)})"

    def _names(self) -> list[str]:
        return [self._list.item(i).data(Qt.UserRole) for i in range(self._list.count())]

    def _current_name(self) -> str | None:
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _select(self, name: str | None) -> None:
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.UserRole) == name:
                self._list.setCurrentRow(i)
                return
        if self._list.count():
            self._list.setCurrentRow(0)

    def _read_back(self) -> None:
        """The palette as the list now says: groups from the dividers, hidden from the ticks."""
        if self._filling:
            return
        groups: list[list[str]] = [[]]
        hidden = set()
        for i in range(self._list.count()):
            item = self._list.item(i)
            name = item.data(Qt.UserRole)
            if name == DIVIDER:
                groups.append([])
            elif name in TOOL_NAMES:
                groups[-1].append(name)
                if item.checkState() != Qt.Checked:
                    hidden.add(name)
        self._update(self._palette.with_changes(groups=[g for g in groups if g], hidden=hidden))

    # ------------------------------------------------------------------ the buttons
    def _refresh_buttons(self) -> None:
        name, row = self._current_name(), self._list.currentRow()
        is_tool = name in TOOL_NAMES
        self._up.setEnabled(row > 0)
        self._down.setEnabled(0 <= row < self._list.count() - 1)
        self._star.setEnabled(is_tool)
        self._star.setText("Unfavorite" if is_tool and name in self._palette.favorites else "Favorite")
        if name == DIVIDER:
            self._divider.setText("Remove divider")
            self._divider.setEnabled(True)
        else:
            self._divider.setText("Add divider")
            self._divider.setEnabled(is_tool and row > 0 and self._list.item(row - 1).data(Qt.UserRole) != DIVIDER)

    def _nudge(self, step: int) -> None:
        row = self._list.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self._list.count():
            return
        item = self._list.takeItem(row)
        self._list.insertItem(target, item)
        self._list.setCurrentRow(target)
        self._read_back()

    def _toggle_favorite(self) -> None:
        name = self._current_name()
        if name in TOOL_NAMES:
            self._update(self._palette.toggled_favorite(name))

    def _toggle_divider(self) -> None:
        name, row = self._current_name(), self._list.currentRow()
        if name == DIVIDER:
            self._list.takeItem(row)
            self._list.setCurrentRow(min(row, self._list.count() - 1))
        elif name in TOOL_NAMES and row > 0:
            self._filling = True
            self._add_divider()
            self._list.insertItem(row, self._list.takeItem(self._list.count() - 1))
            self._filling = False
            self._list.setCurrentRow(row + 1)
        self._read_back()

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt API
        if e.modifiers() & Qt.AltModifier and e.key() in (Qt.Key_Up, Qt.Key_Down):
            self._nudge(-1 if e.key() == Qt.Key_Up else 1)
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------------ saving
    def _update(self, chosen: Palette) -> None:
        if chosen == self._palette:
            return
        self._palette = chosen
        self.changed.emit(chosen)
        palette_store.save(chosen)  # every editor follows; so does this window (_follow)

    def _follow(self) -> None:
        self._palette = palette_store.load()
        QTimer.singleShot(0, self, self._fill)  # after the drop (or click) that caused it has finished

    def palette(self) -> Palette:
        return self._palette

    def list_widget(self) -> QListWidget:
        return self._list

    def done(self, result: int) -> None:  # noqa: N802 - Qt API
        try:
            palette_store.notifier().changed.disconnect(self._follow)
        except (RuntimeError, TypeError):
            pass
        super().done(result)

