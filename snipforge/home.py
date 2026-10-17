"""Library window: browse, reopen, copy and remove past snips."""
from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .icons import icon
from .library import Library

THUMB = QSize(220, 140)


class HomeWindow(QMainWindow):
    def __init__(self, library: Library, on_new_snip, on_open_entry) -> None:
        super().__init__()
        self._library = library
        self._on_open_entry = on_open_entry
        self.setWindowTitle("SnipForge")
        self.resize(1000, 680)

        root = QWidget()
        root.setObjectName("HomeRoot")
        col = QVBoxLayout(root)
        col.setContentsMargins(20, 16, 20, 16)
        header = QHBoxLayout()
        title = QLabel("Your snips")
        title.setProperty("sfRole", "title")
        header.addWidget(title)
        header.addStretch(1)
        hint = QLabel("Win+Shift+S or Print Screen to snip from anywhere")
        hint.setProperty("sfRole", "muted")
        header.addWidget(hint)
        self._folder = QPushButton("Open folder")
        self._folder.clicked.connect(lambda: self._reveal(self._library.root))
        header.addWidget(self._folder)
        self._new = QPushButton("New snip")
        self._new.setProperty("variant", "primary")
        self._new.clicked.connect(on_new_snip)
        header.addWidget(self._new)
        col.addLayout(header)

        self._list = QListWidget()
        self._list.setViewMode(QListWidget.IconMode)
        self._list.setIconSize(THUMB)
        self._list.setGridSize(QSize(THUMB.width() + 24, THUMB.height() + 44))
        self._list.setResizeMode(QListWidget.Adjust)
        self._list.setMovement(QListWidget.Static)
        self._list.setSpacing(6)
        self._list.setSelectionMode(QListWidget.ExtendedSelection)
        self._list.itemActivated.connect(lambda item: self._on_open_entry(item.data(Qt.UserRole)))
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._context_menu)
        col.addWidget(self._list, 1)
        self._empty = QLabel("Your snips will show up here. Press Win+Shift+S to take one.")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setProperty("sfRole", "muted")
        col.addWidget(self._empty)
        self.setCentralWidget(root)
        self._retheme()
        theme.on_change(self._retheme)

    def _retheme(self, _theme=None) -> None:
        self._folder.setIcon(icon("folder"))
        on_accent = theme.current().accent.on
        self._new.setIcon(icon("plus", on_accent, on_accent, on_accent))

    def refresh(self) -> None:
        self._list.clear()
        entries = self._library.entries()
        self._empty.setVisible(not entries)
        for entry in entries:
            pix = QPixmap(str(entry.preview_path))
            if not pix.isNull():
                pix = pix.scaled(THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            item = QListWidgetItem(QIcon(pix), entry.created.strftime("%b %d  %I:%M %p"))
            item.setData(Qt.UserRole, entry.id)
            item.setTextAlignment(Qt.AlignHCenter)
            self._list.addItem(item)

    def showEvent(self, e) -> None:
        self.refresh()
        super().showEvent(e)

    def _selected_ids(self) -> list[str]:
        return [item.data(Qt.UserRole) for item in self._list.selectedItems()]

    def _context_menu(self, pos) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        menu = QMenu(self)
        menu.addAction("Open", lambda: [self._on_open_entry(i) for i in ids])
        if len(ids) == 1:
            menu.addAction("Copy", lambda: self._copy(ids[0]))
            menu.addAction("Show in folder", lambda: self._reveal(self._library.root / ids[0]))
        menu.addSeparator()
        menu.addAction("Move to Recycle Bin", lambda: self._trash(ids))
        menu.exec(self._list.viewport().mapToGlobal(pos))

    def _copy(self, entry_id: str) -> None:
        entry = self._library.get(entry_id)
        if entry:
            QGuiApplication.clipboard().setImage(QImage(str(entry.rendered_path)))

    def _trash(self, ids: list[str]) -> None:
        failed = 0
        for entry_id in ids:
            entry = self._library.get(entry_id)
            if entry and not self._library.move_to_trash(entry):
                failed += 1
        if failed:
            QMessageBox.warning(self, "SnipForge", f"Couldn't move {failed} snip(s) to the Recycle Bin. "
                                "Close any editor showing them and try again.")
        self.refresh()

    @staticmethod
    def _reveal(path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))  # noqa: S606 - opens our own library folder in Explorer
