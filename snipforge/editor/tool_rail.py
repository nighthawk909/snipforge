"""The editor's tool rail: every tool you've kept always has its own button.

The rail shows the palette (palette.py) the way you've arranged it: your favorites in a group at the
top, then your groups in your order, without the tools you've hidden, and a Customize button at the
bottom. One column when the window is tall enough, and two or three when it isn't, so no tool ever
hides behind an overflow arrow. The rail's minimum height is the three-column layout, so the window
can't get too short to show them all. Labels ("Arrow", "Crop") show beside the icons when you've
asked for them and the window is tall enough for a labelled column; when it isn't, the rail goes
back to icons so every tool stays in reach.

From the keyboard the rail is one Tab stop, the tool you're on (as in a Windows toolbar): the arrow
keys, Home and End move between tools, and Enter or Space picks one and goes to the canvas
(`picked`), as Esc does without picking. Clicking a tool never takes the keyboard from the canvas,
where each tool has its own key. Right-click a tool to make it a favorite or hide it, and the rail
itself for labels, its side and Customize tools.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSizePolicy, QToolButton, QWidget

from ..icons import icon
from .palette import Palette, short_title

ICON_SIZE = 20
PAD_X, PAD_Y = 6, 8  # around the buttons
BORDER = 1  # the rail's edge line (styles.py)
SPACING = 2  # between buttons
DIVIDER_GAP = 6  # above and below the line between groups
DIVIDER_INSET = 10  # the line is shorter than the buttons
MAX_COLUMNS = 3
CUSTOMIZE_GAP = 8  # between the last group and the Customize button
BACK = (Qt.Key_Up, Qt.Key_Left)
ON = (Qt.Key_Down, Qt.Key_Right)
PICK = (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space)


class ToolRail(QWidget):
    picked = Signal()  # Enter, Space or Esc on a tool: the keyboard goes back to the canvas
    palette_edited = Signal(object)  # a Palette: the right-click menu changed something; save it
    customize = Signal()  # Customize tools… (the button or the menu)

    def __init__(self, actions: dict[str, QAction], palette: Palette | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ToolRail")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.setAccessibleName("Tools")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu_at)
        self._actions = actions
        self._palette = Palette()
        self._buttons: dict[str, QToolButton] = {}
        self._groups: list[list[QToolButton]] = []
        self._dividers: list[QWidget] = []
        self._columns = 1
        self._labelled = False  # labels are showing (asked for, and there's room)
        self._stop = ""
        self._customize = QToolButton(self)
        self._customize.setObjectName("RailCustomize")
        self._customize.setAutoRaise(True)
        self._customize.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._customize.setCursor(Qt.PointingHandCursor)
        self._customize.setFocusPolicy(Qt.NoFocus)
        self._customize.setToolTip("Customize tools: arrange, hide and favorite them")
        self._customize.setAccessibleName("Customize tools")
        self._customize.clicked.connect(self.customize)
        self._customize.installEventFilter(self)
        for name, action in actions.items():  # once: the actions outlive every layout of the rail
            action.toggled.connect(lambda on, n=name: on and self._set_stop(n))
        self.set_palette(palette or Palette())

    # ------------------------------------------------------------------ the palette
    def set_palette(self, palette: Palette) -> None:
        """Lay the rail out again for `palette`: its groups, favorites, hidden tools and labels."""
        self._palette = palette
        for old in (*self._buttons.values(), *self._dividers):
            old.hide()  # gone at once, not only when the event loop deletes it
            old.setParent(None)
            old.deleteLater()
        self._buttons.clear()
        self._groups = [[self._make_button(name) for name in group] for group in palette.rail_groups()]
        self._dividers = [self._make_divider() for _ in self._groups[1:]]
        checked = next((n for n, b in self._buttons.items() if b.defaultAction().isChecked()), None)
        self._set_stop(checked or "")
        self.retheme()
        self.updateGeometry()
        self._relayout()

    def palette(self) -> Palette:
        return self._palette

    def retheme(self) -> None:
        self._customize.setIcon(icon("sliders"))

    def _make_button(self, name: str) -> QToolButton:
        action = self._actions[name]
        button = QToolButton(self)
        button.setDefaultAction(action)
        button.setText(short_title(name))  # shown only with labels (ToolButtonStyle, in _place)
        button.setAccessibleName(action.text())
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.NoFocus)  # never on a click; one of them is the rail's Tab stop (_set_stop)
        button.setCursor(Qt.PointingHandCursor)
        button.installEventFilter(self)
        button.show()
        if name in self._buttons:  # a favorite also keeps its place in its group: two buttons, one action
            self._buttons[name + "#"] = button
        else:
            self._buttons[name] = button
        return button

    def _make_divider(self) -> QWidget:
        line = QWidget(self)
        line.setObjectName("RailDivider")
        line.setAttribute(Qt.WA_StyledBackground, True)
        line.show()
        return line

    # ------------------------------------------------------------------ keyboard
    def _order(self) -> list[QToolButton]:
        return [*self._buttons.values(), self._customize]

    def _set_stop(self, name: str) -> None:
        """Make `name`'s button the one Tab reaches: a hidden tool's (picked by its key) has none, so
        then the first tool's, or Customize when every tool is hidden."""
        if name != "customize" and name not in self._buttons:
            name = next(iter(self._buttons), "customize")
        self._stop = name
        for other, button in self._buttons.items():
            button.setFocusPolicy(Qt.TabFocus if other == name else Qt.NoFocus)
        self._customize.setFocusPolicy(Qt.TabFocus if name == "customize" else Qt.NoFocus)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        order = self._order()
        if event.type() != QEvent.KeyPress or obj not in order:
            return False
        key = event.key()
        n = order.index(obj)
        if key in BACK + ON + (Qt.Key_Home, Qt.Key_End):
            n = {Qt.Key_Home: 0, Qt.Key_End: len(order) - 1}.get(key, n + (1 if key in ON else -1))
            target = order[max(0, min(len(order) - 1, n))]
            self._set_stop("customize" if target is self._customize else
                           next(name for name, b in self._buttons.items() if b is target))
            target.setFocus(Qt.TabFocusReason)
            return True
        if key in PICK:
            obj.click()
            if obj is not self._customize:
                self.picked.emit()
            return True
        if key == Qt.Key_Escape:
            self.picked.emit()
            return True
        if key == Qt.Key_Menu or (key == Qt.Key_F10 and event.modifiers() & Qt.ShiftModifier):
            self._menu_at(obj.geometry().center())
            return True
        return False

    # ------------------------------------------------------------------ the right-click menu
    def _tool_at(self, pos: QPoint) -> str | None:
        for name, button in self._buttons.items():
            if button.geometry().contains(pos):
                return name.rstrip("#")
        return None

    def _menu_at(self, pos: QPoint) -> None:
        menu = self.menu_for(self._tool_at(pos))
        menu.exec(self.mapToGlobal(pos))
        menu.deleteLater()

    def menu_for(self, name: str | None) -> QMenu:
        """The right-click menu for the tool `name`, or for the rail itself when None."""
        p, menu = self._palette, QMenu(self)
        if name is not None:
            favorite = name in p.favorites
            star = menu.addAction("Remove from favorites" if favorite else "Add to favorites")
            star.triggered.connect(lambda: self.palette_edited.emit(p.toggled_favorite(name)))
            hide = menu.addAction(f"Hide {short_title(name)}")
            hide.setToolTip("Its key still works. Bring it back under Customize tools")
            hide.triggered.connect(lambda: self.palette_edited.emit(p.toggled_hidden(name)))
            menu.addSeparator()
        labels = menu.addAction("Show labels")
        labels.setCheckable(True)
        labels.setChecked(p.labels)
        labels.triggered.connect(lambda on: self.palette_edited.emit(p.with_changes(labels=on)))
        side = menu.addAction("Tools on the right" if p.side == "left" else "Tools on the left")
        side.triggered.connect(lambda: self.palette_edited.emit(
            p.with_changes(side="right" if p.side == "left" else "left")))
        menu.addSeparator()
        custom = menu.addAction("Customize tools…")
        custom.triggered.connect(self.customize)
        reset = menu.addAction("Reset to the default tools")
        reset.setEnabled(not p.is_default())
        reset.triggered.connect(lambda: self.palette_edited.emit(Palette()))
        return menu

    # ------------------------------------------------------------------ queries
    def button(self, name: str) -> QToolButton:
        """The tool's first button from the top: in Favorites if it's one, else in its group."""
        return self._buttons[name]

    def buttons_for(self, name: str) -> list[QToolButton]:
        """Every button for the tool: two for a favorite (Favorites, and its own group)."""
        return [b for key, b in self._buttons.items() if key.rstrip("#") == name]

    def customize_button(self) -> QToolButton:
        return self._customize

    def columns(self) -> int:
        return self._columns

    def height_for_columns(self, columns: int) -> int:
        """The shortest rail that shows every tool in `columns` columns (without the Customize button)."""
        return self._height_for(columns)

    def labelled(self) -> bool:
        return self._labelled

    def shown(self) -> list[str]:
        """The tools on the rail, top to bottom (a favorite twice)."""
        return [key.rstrip("#") for key in self._buttons]

    # ------------------------------------------------------------------ geometry
    def _cell(self) -> int:
        """Every icon button is the same square, big enough for the tallest one's hint. (Its height:
        a labelled button is wider, not taller, so the cell is the same with labels on.)"""
        hints = [b.sizeHint() for b in (*self._buttons.values(), self._customize)]
        return max(h.height() for h in hints) if hints else 32

    def _label_width(self) -> int:
        """The width of the widest labelled button."""
        metrics = self.fontMetrics()
        texts = [b.text() for b in self._buttons.values()] + ["Customize"]
        return self._cell() + 4 + max(metrics.horizontalAdvance(t) for t in texts) + 8

    @staticmethod
    def _rows(group: list[QToolButton], columns: int) -> int:
        return -(-len(group) // columns)

    def _height_for(self, columns: int) -> int:
        cell = self._cell()
        rows = [self._rows(group, columns) for group in self._groups]
        buttons = sum(rows) * cell + max(0, sum(rows) - len(rows)) * SPACING
        return PAD_Y * 2 + buttons + len(self._dividers) * (DIVIDER_GAP * 2 + 1)

    def _customize_fits(self, columns: int) -> bool:
        """Whether the Customize button has a row of its own under the tools. When it hasn't (the
        window is at its shortest), it's in the right-click menu and Ctrl+K instead."""
        return self.height() >= self._height_for(columns) + CUSTOMIZE_GAP + self._cell()

    def _width_for(self, columns: int) -> int:
        if self._labelled:
            return PAD_X * 2 + BORDER + self._label_width()
        return PAD_X * 2 + BORDER + columns * self._cell() + (columns - 1) * SPACING

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(self._width_for(self._columns), self._height_for(1) + CUSTOMIZE_GAP + self._cell())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(self._width_for(self._columns), self._height_for(MAX_COLUMNS))

    def resizeEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(e)
        self._relayout()

    def changeEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().changeEvent(e)
        if e.type() == QEvent.StyleChange:  # a new theme can change the buttons' size
            self.updateGeometry()
            self._relayout()

    def _relayout(self) -> None:
        """The fewest columns that fit the rail's height; labels only when one labelled column does."""
        columns = next((n for n in range(1, MAX_COLUMNS) if self.height() >= self._height_for(n)), MAX_COLUMNS)
        labelled = self._palette.labels and columns == 1
        if columns != self._columns or labelled != self._labelled:
            self._columns, self._labelled = columns, labelled
            self.updateGeometry()  # the window gives the rail its new width
        self._place()

    def _place(self) -> None:
        cell, columns, labelled = self._cell(), self._columns, self._labelled
        width = self._label_width() if labelled else columns * cell + (columns - 1) * SPACING
        style = Qt.ToolButtonTextBesideIcon if labelled else Qt.ToolButtonIconOnly
        for button in (*self._buttons.values(), self._customize):
            button.setToolButtonStyle(style)
        y = PAD_Y
        for n, group in enumerate(self._groups):
            if n:
                y += DIVIDER_GAP
                self._dividers[n - 1].setGeometry(QRect(PAD_X + DIVIDER_INSET, y, width - DIVIDER_INSET * 2, 1))
                y += 1 + DIVIDER_GAP
            for i, button in enumerate(group):
                row, col = divmod(i, columns)
                if labelled:
                    button.setGeometry(PAD_X, y + row * (cell + SPACING), width, cell)
                else:
                    button.setGeometry(PAD_X + col * (cell + SPACING), y + row * (cell + SPACING), cell, cell)
            rows = self._rows(group, columns)
            y += rows * cell + (rows - 1) * SPACING
        self._customize.setText("Customize" if labelled else "")
        self._customize.setVisible(self._customize_fits(columns))
        bottom = max(y + CUSTOMIZE_GAP, self.height() - PAD_Y - cell)
        self._customize.setGeometry(PAD_X, bottom, width if labelled else cell, cell)
