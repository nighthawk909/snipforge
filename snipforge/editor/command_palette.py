"""Find a tool or action by name: Ctrl+K in the editor (⌘K on a Mac).

A small window over the snip with a search box and a list: type a few letters of what you want
("arr", "blur", "rot", "save as") and press Enter. It finds every tool, including ones you've hidden
from the rail, the editor's actions (undo, rotate, resize, save as, grab text, customize tools...)
and the quick styles the current tool can take. Up and Down move through the matches, Enter runs
the one you're on, Esc closes. Each match shows its key, so you learn the shortcut on the way.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..icons import icon
from ..widgets import name_field_buttons

WIDTH = 460
MAX_SHOWN = 12
KINDS = {"tool": "Tool", "action": "Action", "style": "Quick style"}


@dataclass(frozen=True)
class Command:
    label: str                 # what the list shows: "Arrow", "Rotate right", "Red outline"
    run: Callable[[], None]
    kind: str = "action"       # tool | action | style
    key: str = ""              # its shortcut, as people read it ("A", "Ctrl+R")
    icon: str = ""             # an icons.py name, or "" for none
    keywords: tuple[str, ...] = field(default_factory=tuple)  # other words that find it
    color: str = ""            # a quick style's color, shown as a dot

    def words(self) -> list[str]:
        return [w for w in (self.label.lower() + " " + " ".join(self.keywords).lower()).replace("/", " ")
                .replace("…", "").split() if w]


def score(command: Command, query: str) -> int:
    """How well `query` matches: 0 for no match, higher for better. Every word of the query must
    start a word of the command's label or keywords (or be found inside the label)."""
    terms = query.lower().split()
    if not terms:
        return 1
    label, words = command.label.lower(), command.words()
    total = 0
    for term in terms:
        if label.startswith(term):
            total += 30
        elif any(w.startswith(term) for w in words):
            total += 20 if any(w.startswith(term) for w in label.split()) else 10
        elif term in label:
            total += 5
        else:
            return 0
    return total + (3 if command.kind == "tool" else 0)


def ranked(commands: list[Command], query: str) -> list[Command]:
    scored = [(score(c, query), n, c) for n, c in enumerate(commands)]
    return [c for s, _n, c in sorted(((s, n, c) for s, n, c in scored if s), key=lambda t: (-t[0], t[1]))]


class _RowDelegate(QStyledItemDelegate):
    """A match: its icon and name on the left, its kind and key in a quieter color on the right."""

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802 - Qt API
        command: Command = index.data(Qt.UserRole)
        self.initStyleOption(option, index)
        option.text = ""
        style = option.widget.style() if option.widget else QStyle()
        style.drawControl(QStyle.CE_ItemViewItem, option, painter, option.widget)
        t = theme.current()
        selected = bool(option.state & QStyle.State_Selected)
        rect: QRect = option.rect.adjusted(10, 0, -10, 0)
        icon_w = option.decorationSize.width() + 10  # the same indent with or without an icon
        painter.save()
        painter.setPen(QColor(t.accent.text if selected else t.palette.text))
        right = []
        if command.kind != "action":
            right.append(KINDS[command.kind])
        if command.key:
            right.append(command.key)
        tail = "   ".join(right)
        tail_w = painter.fontMetrics().horizontalAdvance(tail) if tail else 0
        room = rect.width() - icon_w - tail_w - 12
        painter.drawText(rect.adjusted(icon_w, 0, -tail_w - 12, 0), Qt.AlignVCenter | Qt.AlignLeft,
                         painter.fontMetrics().elidedText(command.label, Qt.ElideRight, room))
        if tail:
            painter.setPen(QColor(t.accent.text if selected else t.palette.text3))
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignRight, tail)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt API
        return QSize(super().sizeHint(option, index).width(), 32)


class CommandPalette(QDialog):
    def __init__(self, commands: list[Command], parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("CommandPalette")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAccessibleName("Find a tool or action")
        self._commands = commands
        self._shown: list[Command] = []
        self._ran: Command | None = None
        col = QVBoxLayout(self)
        col.setContentsMargins(8, 8, 8, 8)
        col.setSpacing(6)
        self._field = QLineEdit()
        self._field.setObjectName("CommandSearch")
        self._field.setPlaceholderText("Find a tool or action…")
        self._field.setAccessibleName("Find a tool or action")
        self._field.setClearButtonEnabled(True)
        name_field_buttons(self._field, icon_text="Find")
        self._field.textChanged.connect(self._refresh)
        self._field.installEventFilter(self)
        col.addWidget(self._field)
        self._list = QListWidget()
        self._list.setObjectName("CommandList")
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.setIconSize(QSize(18, 18))
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.setAccessibleName("Matches")
        self._list.setItemDelegate(_RowDelegate(self._list))
        self._list.itemClicked.connect(lambda _item: self._run_current())
        col.addWidget(self._list, 1)
        self._empty = QLabel("Nothing matches. Try another word, like the tool's key.")
        self._empty.setProperty("sfRole", "secondary")
        self._empty.setWordWrap(True)
        col.addWidget(self._empty)
        self.setFixedWidth(WIDTH)
        self._refresh("")

    # ------------------------------------------------------------------ showing
    def open_over(self, widget: QWidget) -> None:
        """Show it near the top of `widget`, centered, and start typing."""
        top_left = widget.mapToGlobal(widget.rect().topLeft())
        self.adjustSize()
        self.move(top_left.x() + (widget.width() - self.width()) // 2, top_left.y() + 56)
        self.show()
        self._field.setFocus(Qt.PopupFocusReason)

    def _refresh(self, text: str) -> None:
        self._shown = ranked(self._commands, text)[:MAX_SHOWN]
        self._list.clear()
        t = theme.current()
        for command in self._shown:
            item = QListWidgetItem(self._title(command))  # the text is what screen readers say
            item.setData(Qt.UserRole, command)
            if command.color:
                item.setIcon(_dot(command.color))
            elif command.icon:
                item.setIcon(icon(command.icon, t.palette.text, t.palette.text, t.palette.text))
            item.setToolTip(KINDS.get(command.kind, "") + (f" · {command.key}" if command.key else ""))
            self._list.addItem(item)
        has = bool(self._shown)
        self._list.setVisible(has)
        self._empty.setVisible(not has)
        if has:
            self._list.setCurrentRow(0)
        self._list.setFixedHeight(max(1, min(len(self._shown), MAX_SHOWN)) * 32 + 4)
        self.adjustSize()

    @staticmethod
    def _title(command: Command) -> str:
        key = f"    {command.key}" if command.key else ""
        tag = "" if command.kind == "action" else f"  ·  {KINDS[command.kind]}"
        return f"{command.label}{tag}{key}"

    # ------------------------------------------------------------------ keys
    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        if obj is self._field and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up, Qt.Key_PageDown, Qt.Key_PageUp):
                step = {Qt.Key_Down: 1, Qt.Key_Up: -1, Qt.Key_PageDown: 5, Qt.Key_PageUp: -5}[key]
                row = max(0, min(self._list.count() - 1, self._list.currentRow() + step))
                self._list.setCurrentRow(row)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._run_current()
                return True
            if key == Qt.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, event)

    def _run_current(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        command: Command = item.data(Qt.UserRole)
        self._ran = command
        self.accept()
        command.run()

    # ------------------------------------------------------------------ for tests
    def field(self) -> QLineEdit:
        return self._field

    def shown(self) -> list[Command]:
        return list(self._shown)

    def current(self) -> Command | None:
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def ran(self) -> Command | None:
        return self._ran


def _dot(color: str):
    from PySide6.QtGui import QIcon, QPainter, QPixmap

    pix = QPixmap(36, 36)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(theme.current().palette.border))
    p.setBrush(QColor(color))
    p.drawEllipse(6, 6, 24, 24)
    p.end()
    return QIcon(pix)
