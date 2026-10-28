"""The Layers list: every piece of markup on the snip, top first, with an eye to hide it and a lock
to keep it where it is.

Click a row to select that markup (and selecting markup on the snip picks its row); Move up and
Move down (or Alt+Up and Alt+Down) change what's on top. The list follows the canvas: anything
added, removed, undone, reordered, hidden or locked shows at once.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from ..icons import icon
from .commands import StateCommand

TIE_STEP = 1e-3  # apart enough to order, too little to pass markup at another height
ICON_FOR = {"highlight_box": "highlight", "piece": "move", "erase": "eraser"}
MAX_ROWS_SHOWN = 8  # the list grows to this many rows, then scrolls


def label_for(item) -> str:
    """"Callout: Check why June dipped" — the kind, and a few words of its text if it has any."""
    from .panel import TITLES  # here, not at the top: the panel holds a Layers list

    name = TITLES.get(item.kind, {"highlight_box": "Highlight box"}.get(item.kind, item.kind.title()))
    text = ""
    if hasattr(item, "plain_text"):
        text = item.plain_text()
    elif hasattr(item, "toPlainText"):
        text = item.toPlainText()
    elif item.kind == "step" and hasattr(item, "label"):
        text = item.label()
    text = " ".join(text.split())
    if len(text) > 28:
        text = text[:27] + "…"
    return f"{name}: {text}" if text else name


class _Row(QWidget):
    """A layer's row: icon, name, and its eye and lock."""

    def __init__(self, item, on_hide, on_lock) -> None:
        super().__init__()
        self.item = item
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 4, 2)
        row.setSpacing(6)
        self.icon = QLabel()
        self.icon.setFixedSize(18, 18)
        row.addWidget(self.icon)
        self.name = QLabel()
        row.addWidget(self.name, 1)
        self.eye = QToolButton()
        self.eye.setObjectName("LayerToggle")
        self.eye.setCheckable(True)
        self.eye.setFocusPolicy(Qt.TabFocus)
        self.eye.clicked.connect(on_hide)
        row.addWidget(self.eye)
        self.lock = QToolButton()
        self.lock.setObjectName("LayerToggle")
        self.lock.setCheckable(True)
        self.lock.setFocusPolicy(Qt.TabFocus)
        self.lock.clicked.connect(on_lock)
        row.addWidget(self.lock)
        self.sync()

    def sync(self) -> None:
        """Show the markup as it is now: its name, and whether it's hidden or locked."""
        item = self.item
        shown = (label_for(item), item.hidden, item.locked)
        if shown == getattr(self, "_shown", None):
            return
        self._shown = shown
        self.name.setText(label_for(item))
        role = "muted" if item.hidden else ""
        if self.name.property("sfRole") != role:
            self.name.setProperty("sfRole", role)
            self.name.style().unpolish(self.name)  # the stylesheet looks at it again
            self.name.style().polish(self.name)
        self.eye.setChecked(item.hidden)
        self.eye.setToolTip("Hidden: click to show it" if item.hidden else "Hide it (it stays in the snip's markup)")
        self.eye.setAccessibleName(f"Hide {self.name.text()}")
        self.lock.setChecked(item.locked)
        self.lock.setToolTip("Locked: click to unlock it" if item.locked else "Lock it where it is")
        self.lock.setAccessibleName(f"Lock {self.name.text()}")
        self.retheme()

    def retheme(self) -> None:
        from .. import theme

        t = theme.current()
        self.icon.setPixmap(icon(ICON_FOR.get(self.item.kind, self.item.kind), t.palette.text2).pixmap(18, 18))
        self.eye.setIcon(icon("eye_off" if self.item.hidden else "eye"))
        self.lock.setIcon(icon("lock" if self.item.locked else "unlock"))


class LayersList(QWidget):
    changed = Signal()  # rows were rebuilt (for the panel to size itself)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._canvas = None
        self._syncing = False
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        self._list = QListWidget()
        self._list.setObjectName("LayerList")
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.setFocusPolicy(Qt.TabFocus)
        self._list.setAccessibleName("Layers: the markup on the snip, top first")
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._list.currentRowChanged.connect(self._row_chosen)
        self._list.installEventFilter(self)
        col.addWidget(self._list)
        row = QHBoxLayout()
        row.setSpacing(6)
        self._up = QPushButton("Move up")
        self._up.setToolTip("Bring it over the markup above it (Alt+Up)")
        self._up.clicked.connect(lambda: self.move_current(-1))
        self._down = QPushButton("Move down")
        self._down.setToolTip("Send it under the markup below it (Alt+Down)")
        self._down.clicked.connect(lambda: self.move_current(1))
        row.addWidget(self._up)
        row.addWidget(self._down)
        row.addStretch(1)
        col.addLayout(row)
        self._empty = QLabel("Nothing drawn yet")
        self._empty.setProperty("sfRole", "secondary")
        col.addWidget(self._empty)
        self._items: list = []
        self._stale = False  # the markup changed since the rows were built

    # ------------------------------------------------------------------ the canvas
    def attach(self, canvas) -> None:
        self._canvas = canvas
        canvas.document_changed.connect(self._changed)  # every edit, undo and redo
        canvas.selection_changed.connect(self._follow_selection)
        self.refresh()

    def _changed(self) -> None:
        """Rows are rebuilt once the edit is done (a drag or held key makes many), and only while
        they can be seen."""
        if self._stale:
            return
        self._stale = True
        if self.isVisible():
            QTimer.singleShot(0, self._catch_up)

    def _catch_up(self) -> None:
        if self._stale and isValid(self) and self.isVisible():
            self.refresh()

    def showEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().showEvent(e)
        self._catch_up()

    def _ordered(self) -> list:
        """The canvas's markup, top first."""
        return list(reversed(self._canvas.bottom_up(self._canvas.annotations())))

    def refresh(self) -> None:
        # The canvas outlives the list while a window is torn down, and its signals still fire then.
        if self._canvas is None or not isValid(self._list):
            return
        self._stale = False
        ordered = self._ordered()
        if ordered == self._items:  # the same markup in the same order (a move, a nudge): rows as they are
            for n in range(self._list.count()):
                self._list.itemWidget(self._list.item(n)).sync()
            self._follow_selection()
            return
        self._syncing = True
        current = self._list.currentItem()
        chosen = current.data(Qt.UserRole) if current is not None else None
        self._items = ordered
        self._list.clear()
        for item in self._items:
            entry = QListWidgetItem()
            entry.setData(Qt.UserRole, item)
            entry.setSizeHint(QSize(0, 30))
            entry.setToolTip(label_for(item))
            self._list.addItem(entry)
            widget = _Row(item, lambda _=False, i=item: self.toggle_hidden_item(i),
                          lambda _=False, i=item: self.toggle_locked_item(i))
            self._list.setItemWidget(entry, widget)
        previous = self._list  # Tab: the list, each row's eye and lock top to bottom, then Move up/down
        for n in range(self._list.count()):
            row = self._list.itemWidget(self._list.item(n))
            for button in (row.eye, row.lock):
                QWidget.setTabOrder(previous, button)
                previous = button
        QWidget.setTabOrder(previous, self._up)
        QWidget.setTabOrder(self._up, self._down)
        rows = len(self._items)
        self._list.setVisible(rows > 0)
        self._empty.setVisible(rows == 0)
        self._list.setFixedHeight(min(rows, MAX_ROWS_SHOWN) * 30 + 6 if rows else 0)
        self._syncing = False
        self._follow_selection(chosen)
        self.changed.emit()

    def _follow_selection(self, prefer=None) -> None:
        if self._canvas is None or self._syncing or not isValid(self._list):
            return
        if self._stale:
            return  # the rows are about to be rebuilt, and they follow the selection then
        selected = self._canvas.selected_annotations()
        target = selected[0] if selected else None
        self._syncing = True
        row = self._items.index(target) if target in self._items else -1
        self._list.setCurrentRow(row)
        self._syncing = False
        self._refresh_buttons()

    def _row_chosen(self, row: int) -> None:
        if self._syncing or self._canvas is None:
            return
        item = self._items[row] if 0 <= row < len(self._items) else None
        if item is not None and not item.hidden:
            self._canvas._select([item])
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        row = self._list.currentRow()
        self._up.setEnabled(row > 0)
        self._down.setEnabled(0 <= row < len(self._items) - 1)

    # ------------------------------------------------------------------ actions
    def move_current(self, step: int) -> None:
        """Swap the current layer with the one above (-1) or below (1) it: one undo step."""
        row = self._list.currentRow()
        other = row + step
        if self._canvas is None or row < 0 or not 0 <= other < len(self._items):
            return
        a, b = self._items[row], self._items[other]
        # Markup at the same height (drawn in order, or every spotlight) gets heights of its own first,
        # just apart and in the order shown, so the swap moves it one row and no further
        ties = [i for i in self._items if i.zValue() == a.zValue()] if a.zValue() == b.zValue() else []
        changed = list(dict.fromkeys([a, b, *ties]))
        before = {i: i.get_state() for i in changed}
        for k, item in enumerate(ties):
            item.setZValue(item.zValue() + (len(ties) - 1 - k) * TIE_STEP)
        za, zb = a.zValue(), b.zValue()
        a.setZValue(zb)
        b.setZValue(za)
        records = [(i, before[i], i.get_state()) for i in changed]
        self._canvas.undo_stack.push(StateCommand(records, "Reorder"))
        self.refresh()
        self._syncing = True
        self._list.setCurrentRow(self._items.index(a))
        self._syncing = False
        self._refresh_buttons()

    def toggle_hidden_item(self, item) -> None:
        self._canvas.set_hidden([item], not item.hidden)
        self.refresh()

    def toggle_locked_item(self, item) -> None:
        self._canvas.set_locked([item], not item.locked)
        self.refresh()

    def toggle_hidden(self, row: int) -> None:
        self.toggle_hidden_item(self._items[row])

    def toggle_locked(self, row: int) -> None:
        self.toggle_locked_item(self._items[row])

    def eventFilter(self, watched, e) -> bool:  # noqa: N802 - Qt API
        """Alt+Up and Alt+Down in the list (which would take them as plain arrows) move the layer."""
        if (watched is self._list and e.type() == QEvent.KeyPress and e.modifiers() & Qt.AltModifier
                and e.key() in (Qt.Key_Up, Qt.Key_Down)):
            self.move_current(-1 if e.key() == Qt.Key_Up else 1)
            return True
        return super().eventFilter(watched, e)

    def retheme(self) -> None:
        for n in range(self._list.count()):
            widget = self._list.itemWidget(self._list.item(n))
            if widget is not None:
                widget.retheme()

    def has_rows(self) -> bool:
        """Whether there's markup to list (asked before the list is shown, so not from its rows)."""
        return self._canvas is not None and bool(self._canvas.annotations())

    # ------------------------------------------------------------------ for tests
    def names(self) -> list[str]:
        if self._stale:
            self.refresh()
        return [label_for(i) for i in self._items]

    def list_widget(self) -> QListWidget:
        return self._list

    def row_hidden(self, row: int) -> bool:
        return self._items[row].hidden

    def row_locked(self, row: int) -> bool:
        return self._items[row].locked
