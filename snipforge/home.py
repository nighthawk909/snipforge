"""Library window: every snip as a card, grouped by day, with search, favorites and tags."""
from __future__ import annotations

import datetime as dt
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QLocale, QMimeData, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .icons import icon
from .library import DOCUMENT, META, EntryMeta, Library
from .library_search import PERIODS, Item, group_by_date, select, tag_counts, top_apps
from .library_view import SnipGrid
from .widgets import NavItem, Segmented

log = logging.getLogger(__name__)
VIEWS = {"all": ("All captures", "grid"), "favorites": ("Favorites", "star"), "edited": ("Recently edited", "pen")}
PERIOD_LABELS = {"any": "Any time", "today": "Today", "week": "This week", "month": "This month"}
TAG_COLORS = ("#2F7BF6", "#2FA85A", "#F28C28", "#8E5BE8", "#E5372D", "#0E7C86")
SIDEBAR_WIDTH = 236
SEARCH_DELAY_MS = 150
CONFIRM_TRASH_OVER = 20


def _stamp(path, ns: bool):
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_mtime_ns if ns else info.st_mtime


class HomeWindow(QMainWindow):
    def __init__(self, library: Library, on_new_snip, on_open_entry, indexer=None, on_settings=None) -> None:
        super().__init__()
        self._library = library
        self._on_open_entry = on_open_entry
        self._on_settings = on_settings
        self._indexer = indexer
        self._items: list[Item] = []
        self._meta_cache: dict[str, tuple[int, object]] = {}
        self._view, self._tag, self._period, self._app = "all", None, "any", None
        self.setWindowTitle("SnipForge")
        self.resize(1180, 760)

        root = QWidget()
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._build_sidebar(on_new_snip))
        row.addWidget(self._build_main(on_new_snip), 1)
        self.setCentralWidget(root)
        self._build_shortcuts()
        if indexer is not None:
            indexer.indexed.connect(self._on_indexed)
        self._retheme()
        theme.on_change(self._retheme)

    # ------------------------------------------------------------------ layout
    def _build_sidebar(self, on_new_snip) -> QWidget:
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setAttribute(Qt.WA_StyledBackground, True)
        side.setFixedWidth(SIDEBAR_WIDTH)
        col = QVBoxLayout(side)
        col.setContentsMargins(12, 16, 12, 12)
        col.setSpacing(2)
        self._new = QPushButton("New capture")
        self._new.setProperty("variant", "primary")
        self._new.setMinimumHeight(38)
        self._new.clicked.connect(on_new_snip)
        col.addWidget(self._new)
        col.addSpacing(12)
        self._nav: dict[str, NavItem] = {}
        for key, (label, icon_name) in VIEWS.items():
            item = NavItem(label, icon_name, on_band=True)
            item.clicked.connect(lambda _=False, k=key: self.show_view(k))
            self._nav[key] = item
            col.addWidget(item)
        col.addSpacing(14)
        self._tags_label = QLabel("TAGS")
        self._tags_label.setProperty("sfRole", "section")
        self._tags_label.setContentsMargins(12, 0, 0, 4)
        col.addWidget(self._tags_label)
        self._tags_box = QVBoxLayout()
        self._tags_box.setSpacing(2)
        col.addLayout(self._tags_box)
        col.addStretch(1)
        self._folder = NavItem("Open library folder", "folder", on_band=True)
        self._folder.setCheckable(False)
        self._folder.clicked.connect(lambda: self._reveal(self._library.root))
        col.addWidget(self._folder)
        self._bin = NavItem("Recycle Bin", "trash", on_band=True)
        self._bin.setCheckable(False)
        self._bin.setToolTip("Snips you delete go to the Windows Recycle Bin, so you can restore them")
        self._bin.clicked.connect(self._open_recycle_bin)
        col.addWidget(self._bin)
        self._settings = NavItem("Settings", "settings", on_band=True)
        self._settings.setCheckable(False)
        self._settings.setVisible(self._on_settings is not None)
        if self._on_settings is not None:
            self._settings.clicked.connect(lambda _=False: self._on_settings())  # don't pass `checked` along
        col.addWidget(self._settings)
        return side

    def _build_main(self, on_new_snip) -> QWidget:
        main = QWidget()
        col = QVBoxLayout(main)
        col.setContentsMargins(24, 18, 16, 0)
        col.setSpacing(12)
        head = QHBoxLayout()
        self._title = QLabel("All captures")
        self._title.setProperty("sfRole", "title")
        head.addWidget(self._title)
        self._count = QLabel()
        self._count.setProperty("sfRole", "muted")
        head.addWidget(self._count)
        head.addStretch(1)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search words in your snips, apps or tags")
        self._search.setAccessibleName("Search snips")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(320)
        self._search_icon = self._search.addAction(icon("search"), QLineEdit.LeadingPosition)
        self._search_timer = QTimer(self, singleShot=True, interval=SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self.apply)
        self._search.textChanged.connect(lambda _t: self._search_timer.start())
        self._indexed_timer = QTimer(self, singleShot=True, interval=1000)
        self._indexed_timer.timeout.connect(self.apply)
        head.addWidget(self._search)
        col.addLayout(head)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self._periods = Segmented([(k, PERIOD_LABELS[k]) for k in PERIODS], value="any")
        self._periods.changed.connect(self._set_period)
        filters.addWidget(self._periods)
        self._apps_box = QHBoxLayout()
        self._apps_box.setSpacing(6)
        filters.addLayout(self._apps_box)
        filters.addStretch(1)
        col.addLayout(filters)

        self._stack = QStackedWidget()
        self.grid = SnipGrid()
        self.grid.activated.connect(lambda index: self._open_ids([self.grid.current_id()]))
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._context_menu)
        self.grid.delegate.favorite_clicked.connect(self.toggle_favorite)
        self.grid.delegate.copy_clicked.connect(self._copy)
        self._stack.addWidget(self.grid)
        empty = QWidget()
        empty_col = QVBoxLayout(empty)
        empty_col.addStretch(1)
        self._empty_title = QLabel()
        self._empty_title.setProperty("sfRole", "heading")
        self._empty_title.setAlignment(Qt.AlignCenter)
        self._empty_hint = QLabel()
        self._empty_hint.setProperty("sfRole", "secondary")
        self._empty_hint.setAlignment(Qt.AlignCenter)
        self._empty_hint.setWordWrap(True)
        self._empty_button = QPushButton("New capture")
        self._empty_button.setProperty("variant", "primary")
        self._empty_button.clicked.connect(on_new_snip)
        for w in (self._empty_title, self._empty_hint):
            empty_col.addWidget(w)
        empty_col.addWidget(self._empty_button, 0, Qt.AlignHCenter)
        empty_col.addStretch(2)
        self._stack.addWidget(empty)
        col.addWidget(self._stack, 1)
        return main

    def _build_shortcuts(self) -> None:
        for keys, slot in ((QKeySequence.Find, self._focus_search),
                           (QKeySequence.Delete, lambda: self._trash(self.grid.selected_ids())),
                           (QKeySequence.Copy, self._copy_current)):
            QShortcut(keys, self, slot)
        # Only while the grid has focus, so typing in the search box never stars or tags anything.
        for keys, slot in (("F", self._favorite_current), ("T", self._tags_current)):
            QShortcut(QKeySequence(keys), self.grid, slot, Qt.WidgetShortcut)

    def _retheme(self, _theme=None) -> None:
        self._search_icon.setIcon(icon("search"))
        on = theme.current().header.primary_text
        self._new.setIcon(icon("plus", on, on, on))
        self.update()

    # ------------------------------------------------------------------ data
    def refresh(self) -> None:
        """Re-read the library folder; only snips whose details changed are read again."""
        known = {i.entry.id: i for i in self._items}
        items = []
        for snap in self._library.scan():
            old = known.get(snap.entry.id)
            meta = self._meta_for(snap.entry, snap.meta_stamp, old.meta if old else None)
            created = old.created if old else snap.entry.created
            duration = old.duration if old and old.duration is not None else self._duration(snap.entry)
            items.append(Item(snap.entry, created, meta, snap.edited_at, duration))
        self._items = items
        self._after_change()

    def update_entry(self, entry_id: str) -> None:
        """One snip changed (taken, edited, starred, tagged or removed): read just that one again."""
        old = next((i for i in self._items if i.entry.id == entry_id), None)
        rest = [i for i in self._items if i.entry.id != entry_id]
        entry = self._library.get(entry_id)
        if entry is not None:
            meta = self._meta_for(entry, _stamp(entry.path / META, ns=True), old.meta if old else None)
            rest.append(Item(entry, old.created if old else entry.created, meta,
                             _stamp(entry.path / DOCUMENT, ns=False), self._duration(entry)))
            rest.sort(key=lambda i: i.entry.id, reverse=True)
        self._items = rest
        self._after_change()

    def _meta_for(self, entry, stamp, previous):
        """A snip's details, cached by meta.json's modification time. If the file is locked for a
        moment, keep what we had and read it again next time."""
        cached = self._meta_cache.get(entry.id)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        if stamp is None:
            meta = EntryMeta()
        else:
            try:
                meta = self._library.read_meta(entry)
            except OSError:
                return previous or EntryMeta()
        self._meta_cache[entry.id] = (stamp, meta)
        return meta

    def _duration(self, entry) -> float | None:
        """A recording's length (read once: a finished recording doesn't change); None for a snip."""
        return self._library.recording_details(entry).duration if entry.is_recording else None

    def _meta(self, entry):
        return self._meta_for(entry, _stamp(entry.path / META, ns=True), None)

    def _after_change(self) -> None:
        self._refresh_sidebar()
        self._refresh_app_chips()
        self.apply()

    def _refresh_sidebar(self) -> None:
        counts = {"all": len(self._items), "favorites": sum(i.meta.favorite for i in self._items),
                  "edited": sum(i.edited_at is not None for i in self._items)}
        for key, item in self._nav.items():
            item.set_count(counts[key])
            item.setChecked(key == self._view and self._tag is None)
        while self._tags_box.count():
            widget = self._tags_box.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        tags = tag_counts(self._items)
        self._tags_label.setVisible(bool(tags))
        if self._tag is not None and self._tag not in dict(tags):
            self._tag = None
        for n, (tag, count) in enumerate(tags[:12]):
            nav = NavItem(tag, dot=TAG_COLORS[n % len(TAG_COLORS)], on_band=True)  # by position: neighbors differ
            nav.set_count(count)
            nav.setChecked(tag == self._tag)
            nav.clicked.connect(lambda _=False, t=tag: self.show_tag(t))
            self._tags_box.addWidget(nav)

    def _refresh_app_chips(self) -> None:
        while self._apps_box.count():
            widget = self._apps_box.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        apps = top_apps(self._items)
        if self._app is not None and self._app not in apps:
            self._app = None
        for app in apps:
            chip = QToolButton()
            chip.setObjectName("Chip")
            chip.setText(app)
            chip.setCheckable(True)
            chip.setChecked(app == self._app)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(f"Only snips from {app}")
            chip.clicked.connect(lambda on, a=app: self._set_app(a if on else None))
            self._apps_box.addWidget(chip)

    def apply(self) -> None:
        """Show the snips matching the current view, tag, period, app and search. Keeps the selection,
        the keyboard position and the scroll position."""
        now = dt.datetime.now()
        query = self._search.text().strip()
        first_weekday = QLocale().firstDayOfWeek().value  # ISO: 1 = Monday ... 7 = Sunday
        chosen = select(self._items, view=self._view, tag=self._tag, period=self._period, app=self._app,
                        query=query, now=now, first_weekday=first_weekday)
        if self._view == "edited":
            groups = [("Recently edited", chosen)] if chosen else []
        else:
            groups = [(label, [chosen[n] for n in rows])
                      for label, rows in group_by_date([i.created for i in chosen], now, first_weekday)]
        current, selected = self.grid.current_id(), self.grid.selected_ids()
        scroll = self.grid.verticalScrollBar().value()
        self.grid.model_.set_groups(groups)
        self.grid.restore(current, selected, scroll)
        self._title.setText(f"#{self._tag}" if self._tag else VIEWS[self._view][0])
        self._count.setText(f"{len(chosen)} snip{'s' if len(chosen) != 1 else ''}")
        self._show_empty_state(chosen, query)

    def _show_empty_state(self, chosen: list[Item], query: str) -> None:
        if chosen:
            self._stack.setCurrentIndex(0)
            return
        self._stack.setCurrentIndex(1)
        if not self._items:
            self._empty_title.setText("Your snips will show up here")
            self._empty_hint.setText("Press Win+Shift+S or Print Screen to snip from anywhere.")
            self._empty_button.show()
        else:
            self._empty_title.setText(f"No snips match “{query}”" if query else "Nothing here yet")
            self._empty_hint.setText("Search looks at the words inside your snips, the app they came from "
                                     "and your tags." if query else "Try another view or clear the filters.")
            self._empty_button.hide()

    # ------------------------------------------------------------------ navigation
    def show_view(self, view: str) -> None:
        self._view, self._tag = view, None
        self._refresh_sidebar()
        self.apply()

    def show_tag(self, tag: str) -> None:
        self._view, self._tag = "all", tag
        self._refresh_sidebar()
        self.apply()

    def _set_period(self, period: str) -> None:
        self._period = period
        self.apply()

    def _set_app(self, app: str | None) -> None:
        self._app = app
        for n in range(self._apps_box.count()):
            chip = self._apps_box.itemAt(n).widget()
            if chip:
                chip.setChecked(chip.text() == app)
        self.apply()

    def _focus_search(self) -> None:
        self._search.setFocus()
        self._search.selectAll()

    # ------------------------------------------------------------------ actions
    def showEvent(self, e) -> None:  # noqa: N802 - Qt API
        self.refresh()
        super().showEvent(e)
        if self._indexer is not None:
            self._indexer.backfill([i.entry for i in self._items if not i.meta.indexed])

    def hideEvent(self, e) -> None:  # noqa: N802 - Qt API
        self.grid.delegate.clear_cache()  # SnipForge keeps running in the tray: don't hold thumbnails
        super().hideEvent(e)

    def _on_indexed(self, entry_id: str) -> None:
        entry = self._library.get(entry_id)
        if entry is None:
            return
        meta = self._meta(entry)
        self._items = [replace(i, meta=meta) if i.entry.id == entry_id else i for i in self._items]
        if self._search.text().strip():
            self._indexed_timer.start()  # new words may match the search; regroup at most once a second
        else:
            self.grid.model_.update_meta(entry_id, meta)

    def toggle_favorite(self, entry_id: str) -> None:
        entry = self._library.get(entry_id)
        if entry is None:
            return
        try:
            self._library.update_meta(entry, favorite=not self._meta(entry).favorite)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "SnipForge", f"Couldn't update that snip: {exc}")
            return
        self.update_entry(entry_id)

    def _copy_current(self) -> None:
        entry_id = self.grid.current_id()
        if entry_id:
            self._copy(entry_id)

    def _favorite_current(self) -> None:
        entry_id = self.grid.current_id()
        if entry_id:
            self.toggle_favorite(entry_id)

    def _tags_current(self) -> None:
        entry_id = self.grid.current_id()
        if entry_id:
            self.edit_tags(entry_id)

    def edit_tags(self, entry_id: str) -> None:
        entry = self._library.get(entry_id)
        if entry is None:
            return
        current = ", ".join(self._meta(entry).tags)
        text, ok = QInputDialog.getText(self, "Tags", "Tags for this snip, separated by commas:", text=current)
        if not ok:
            return
        try:
            self._library.update_meta(entry, tags=[t for t in text.split(",")])
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "SnipForge", f"Couldn't update that snip: {exc}")
            return
        self.update_entry(entry_id)

    def _context_menu(self, pos) -> None:
        index = self.grid.indexAt(pos)
        if not index.isValid():  # empty space: never act on a selection you can't see under the pointer
            self.grid.clearSelection()
            return
        if not self.grid.selectionModel().isSelected(index):
            self.grid.setCurrentIndex(index)
        ids = self.grid.selected_ids()
        if not ids:
            return
        menu = QMenu(self)
        menu.addAction(icon("image"), "Open" if len(ids) == 1 else f"Open {len(ids)} snips",
                       lambda: self._open_ids(ids))
        if len(ids) == 1:
            entry_id = ids[0]
            menu.addAction(icon("copy"), "Copy", lambda: self._copy(entry_id))
            menu.addAction(icon("folder"), "Show in folder", lambda: self._reveal(self._library.root / entry_id))
            menu.addSeparator()
            favorite = self._meta(self._library.get(entry_id)).favorite if self._library.get(entry_id) else False
            menu.addAction(icon("star"), "Remove from favorites\tF" if favorite else "Add to favorites\tF",
                           lambda: self.toggle_favorite(entry_id))
            menu.addAction(icon("tag"), "Tags…\tT", lambda: self.edit_tags(entry_id))
        menu.addSeparator()
        menu.addAction(icon("trash"), "Move to Recycle Bin", lambda: self._trash(ids))
        menu.exec(self.grid.viewport().mapToGlobal(pos))

    def _open_ids(self, ids: list[str | None]) -> None:
        for entry_id in ids:
            if entry_id:
                self._on_open_entry(entry_id)

    def _copy(self, entry_id: str) -> None:
        entry = self._library.get(entry_id)
        if entry is None:
            return
        if entry.is_recording:  # the video file, to paste into an email, a chat or a folder
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(entry.recording_path))])
            QGuiApplication.clipboard().setMimeData(mime)
            return
        image = QImage(str(entry.rendered_path))
        if image.isNull():
            QMessageBox.warning(self, "SnipForge", "Couldn't read that snip.")
            return
        QGuiApplication.clipboard().setImage(image)

    def _trash(self, ids: list[str]) -> None:
        if not ids:
            return
        question = self._trash_question(ids)
        if question and QMessageBox.question(self, "SnipForge", question) != QMessageBox.Yes:
            return
        failed, gone = 0, set()
        for entry_id in ids:
            entry = self._library.get(entry_id)
            if entry and not self._library.move_to_trash(entry):
                failed += 1
            else:
                gone.add(entry_id)
        if failed:
            QMessageBox.warning(self, "SnipForge", f"Couldn't move {failed} snip(s) to the Recycle Bin. "
                                "Close any editor showing them and try again.")
        self._items = [i for i in self._items if i.entry.id not in gone]
        self._after_change()

    def _trash_question(self, ids: list[str]) -> str | None:
        """What to ask before moving these to the Recycle Bin: many at once, or recordings with trimmed
        copies or GIFs saved beside them (which go too); None to go ahead."""
        exports = [name for entry_id in ids if (entry := self._library.get(entry_id))
                   for name in self._library.exports(entry)]
        if not exports:
            return f"Move {len(ids)} snips to the Recycle Bin?" if len(ids) > CONFIRM_TRASH_OVER else None
        named = ", ".join(exports[:3]) + (f" and {len(exports) - 3} more" if len(exports) > 3 else "")
        what = "this recording" if len(ids) == 1 else f"these {len(ids)} items"
        return (f"Move {what} to the Recycle Bin?\n\nThe trimmed copies and GIFs saved with it go too: "
                f"{named}.")

    def _open_recycle_bin(self) -> None:
        try:
            if sys.platform == "win32":
                os.startfile("shell:RecycleBinFolder")  # noqa: S606 - opens the Windows Recycle Bin
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path.home() / ".Trash")))  # Finder
        except OSError as exc:
            log.info("Couldn't open the Recycle Bin: %s", exc)

    @staticmethod
    def _reveal(path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 - opens our own library folder in Explorer
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))  # Finder

    # compatibility with callers that list selected snips
    def _selected_ids(self) -> list[str]:
        return self.grid.selected_ids()
