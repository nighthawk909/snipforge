"""The Combine window: the snips to put together (in order, with Add, Remove and Move up and down),
how (side by side, stacked, a grid, numbered steps with captions), and a preview of the result."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import combine
from .library import Library
from .widgets import Segmented, SettingRow, ToggleSwitch, stack

PREVIEW_SIDE = 520  # the preview is drawn from copies of the pictures this size at most
PREVIEW_LONGEST = 900  # and is at most this many pixels on a side
THUMB = QSize(96, 64)
PICK_LIMIT = 500  # snips offered by Add snips (the newest)


class _CaptionEdit(QLineEdit):
    """Enter goes on to the next step's caption, instead of combining (the window's default button)."""

    next_step = Signal()

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt API
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.next_step.emit()
            e.accept()
            return
        super().keyPressEvent(e)


def _thumb(image: QImage) -> QIcon:
    return QIcon(QPixmap.fromImage(image.scaled(THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)))


def _small(image: QImage) -> QImage:
    if max(image.width(), image.height()) <= PREVIEW_SIDE:
        return image
    return image.scaled(PREVIEW_SIDE, PREVIEW_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class SnipPicker(QDialog):
    """Add snips: the Library's newest snips, pick one or more."""

    def __init__(self, library: Library, exclude: set[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add snips")
        self.resize(640, 480)
        col = QVBoxLayout(self)
        col.addWidget(QLabel("Choose the snips to add (Ctrl or Shift to choose several)."))
        self.snips = QListWidget()
        self.snips.setAccessibleName("Snips in your Library")
        self.snips.setViewMode(QListWidget.IconMode)
        self.snips.setIconSize(THUMB)
        self.snips.setResizeMode(QListWidget.Adjust)
        self.snips.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for entry in library.entries(limit=PICK_LIMIT):
            if entry.id in exclude:
                continue
            item = QListWidgetItem(_thumb(QImage(str(entry.preview_path))), entry.created.strftime("%d %b %H:%M"))
            item.setData(Qt.UserRole, entry.id)
            item.setToolTip(entry.created.strftime("%A %d %B %Y, %H:%M:%S"))
            self.snips.addItem(item)
        self.snips.itemDoubleClicked.connect(lambda _item: self.accept())
        col.addWidget(self.snips, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.add_button = QPushButton("Add")
        self.add_button.setProperty("variant", "primary")
        self.add_button.setDefault(True)
        self.add_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.add_button)
        col.addLayout(buttons)

    def chosen(self) -> list[str]:
        return [item.data(Qt.UserRole) for item in self.snips.selectedItems()]


class CombineDialog(QDialog):
    """`library` and the ids of the snips to start with (in order; MAX_IMAGES at most). After
    Combine, `combined` is the combined image and its markup (snipforge.combine.Combined)."""

    def __init__(self, library: Library, entry_ids: list[str], parent=None) -> None:
        super().__init__(parent)
        self._library = library
        self._images: dict[str, QImage] = {}
        self._previews: dict[str, QImage] = {}
        self._sizes: dict[str, QSize] = {}
        self.combined: combine.Combined | None = None
        self.setWindowTitle("Combine snips")
        self.setObjectName("CombineDialog")
        self.resize(1080, 680)
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 18, 20, 16)
        row.setSpacing(16)
        row.addLayout(self._build_list(), 0)
        row.addLayout(self._build_options(), 1)
        self._redraw = QTimer(self, singleShot=True, interval=120)
        self._redraw.timeout.connect(self._show_preview)
        skipped = 0
        for entry_id in entry_ids:
            if len(self.ids()) >= combine.MAX_IMAGES or not self._add(entry_id):
                skipped += 1
        if skipped:
            self._note_skipped(skipped)
        if self.pictures.count():
            self.pictures.setCurrentRow(0)
        self._changed()

    # ------------------------------------------------------------------ the pictures
    def _build_list(self) -> QVBoxLayout:
        col = QVBoxLayout()
        heading = QLabel("Snips, in order")
        heading.setProperty("sfRole", "heading")
        col.addWidget(heading)
        self.pictures = QListWidget()
        self.pictures.setAccessibleName("Snips to combine, in order")
        self.pictures.setIconSize(THUMB)
        self.pictures.setFixedWidth(280)
        self.pictures.setDragDropMode(QAbstractItemView.InternalMove)  # drag to reorder
        self.pictures.model().rowsMoved.connect(lambda *_: self._changed())
        self.pictures.currentRowChanged.connect(self._show_caption)
        col.addWidget(self.pictures, 1)
        self.note = QLabel()
        self.note.setProperty("sfRole", "muted")
        self.note.setWordWrap(True)
        self.note.setMaximumWidth(280)
        self.note.hide()
        col.addWidget(self.note)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("Add snips…")
        self.add_button.clicked.connect(self.add_snips)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_current)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        col.addLayout(buttons)
        moves = QHBoxLayout()
        self.up_button = QPushButton("Move up")
        self.up_button.clicked.connect(lambda: self.move_current(-1))
        self.down_button = QPushButton("Move down")
        self.down_button.clicked.connect(lambda: self.move_current(1))
        moves.addWidget(self.up_button)
        moves.addWidget(self.down_button)
        col.addLayout(moves)
        return col

    def _add(self, entry_id: str) -> bool:
        entry = self._library.get(entry_id)
        if entry is None or entry.is_recording or entry_id in self.ids():
            return False
        image = QImage(str(entry.rendered_path))  # the finished snip, markup and all
        if image.isNull():
            return False
        self._images[entry_id], self._previews[entry_id] = image, _small(image)
        self._sizes[entry_id] = image.size()
        item = QListWidgetItem(_thumb(image), "")
        item.setData(Qt.UserRole, entry_id)
        item.setData(Qt.UserRole + 1, "")  # its caption, for numbered steps
        item.setToolTip(entry.created.strftime("%A %d %B %Y, %H:%M:%S"))
        self.pictures.addItem(item)
        return True

    def ids(self) -> list[str]:
        return [self.pictures.item(n).data(Qt.UserRole) for n in range(self.pictures.count())]

    def captions(self) -> tuple[str, ...]:
        return tuple(self.pictures.item(n).data(Qt.UserRole + 1) or "" for n in range(self.pictures.count()))

    def add_snips(self) -> None:
        picker = SnipPicker(self._library, set(self.ids()), self)
        picker.setWindowModality(Qt.WindowModal)
        try:
            if picker.exec() != QDialog.Accepted:
                return
            chosen = picker.chosen()
            added = [entry_id for entry_id in chosen
                     if len(self.ids()) < combine.MAX_IMAGES and self._add(entry_id)]
        finally:
            picker.deleteLater()
        if len(added) < len(chosen):
            self._note_skipped(len(chosen) - len(added))
        if added:
            self.pictures.setCurrentRow(self.pictures.count() - 1)
            self._changed()

    def _note_skipped(self, count: int) -> None:
        self.note.setText(f"{count} snip{'s' if count != 1 else ''} left out: up to {combine.MAX_IMAGES} "
                          "can be combined, and recordings and snips that can't be read are left out.")
        self.note.show()

    def remove_current(self) -> None:
        row = self.pictures.currentRow()
        if row >= 0:
            item = self.pictures.takeItem(row)
            for kept in (self._images, self._previews, self._sizes):
                kept.pop(item.data(Qt.UserRole), None)
            self._changed()

    def move_current(self, step: int) -> None:
        row = self.pictures.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.pictures.count():
            return
        item = self.pictures.takeItem(row)
        self.pictures.insertItem(target, item)
        self.pictures.setCurrentRow(target)
        self._changed()

    # ------------------------------------------------------------------ how
    def _build_options(self) -> QVBoxLayout:
        col = QVBoxLayout()
        self.layout_choice = Segmented(list(combine.LAYOUTS.items()), value="row")
        self.layout_choice.setAccessibleName("Layout")
        self.layout_choice.changed.connect(lambda _v: self._changed())
        self.columns = QSpinBox()
        self.columns.setRange(2, 6)
        self.columns.setValue(2)
        self.columns.setAccessibleName("Columns")
        self.columns.valueChanged.connect(lambda _v: self._changed())
        self.columns_row = SettingRow("Columns", "", self.columns)
        self.gap = Segmented([(str(k), v) for k, v in combine.GAPS.items()], value="12")
        self.gap.setAccessibleName("Space between")
        self.gap.changed.connect(lambda _v: self._changed())
        self.background = Segmented([("white", "White"), ("light", "Light grey"), ("dark", "Dark"),
                                     ("none", "Transparent")], value="white")
        self.background.setAccessibleName("Background")
        self.background.changed.connect(lambda _v: self._changed())
        self.same_size = ToggleSwitch("The same size")
        self.same_size.setChecked(True)
        self.same_size.toggled.connect(lambda _on: self._changed())
        self.caption = _CaptionEdit()
        self.caption.next_step.connect(self._next_caption)
        self.caption.setPlaceholderText("What this step shows (optional). Enter: the next step")
        self.caption.setAccessibleName("Caption for the selected step")
        self.caption.setMinimumWidth(340)
        self.caption.textEdited.connect(self._set_caption)
        self.caption_row = SettingRow("Caption", "For the snip selected on the left", self.caption)
        col.addWidget(stack(
            SettingRow("Layout", "", self.layout_choice),
            self.columns_row,
            self.caption_row,
            SettingRow("The same size", "Shrinks bigger snips to match the smallest (never enlarges)", self.same_size),
            SettingRow("Space between", "", self.gap),
            SettingRow("Background", "", self.background)))
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(240)
        self.preview.setAccessibleName("Preview of the combined image")
        self.preview.setProperty("sfRole", "secondary")
        col.addWidget(self.preview, 1)
        self.size_label = QLabel()
        self.size_label.setProperty("sfRole", "muted")
        col.addWidget(self.size_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.combine_button = QPushButton("Combine")
        self.combine_button.setProperty("variant", "primary")
        self.combine_button.setDefault(True)
        self.combine_button.clicked.connect(self.combine_now)
        buttons.addWidget(cancel)
        buttons.addWidget(self.combine_button)
        col.addLayout(buttons)
        return col

    def options(self) -> combine.Options:
        return combine.Options(layout=self.layout_choice.value, gap=int(self.gap.value),
                               background=self.background.value, columns=self.columns.value(),
                               same_size=self.same_size.isChecked(), captions=self.captions())

    def _show_caption(self, row: int) -> None:
        item = self.pictures.item(row) if row >= 0 else None
        self.caption.setText(item.data(Qt.UserRole + 1) if item is not None else "")
        self.caption.setEnabled(item is not None)

    def _next_caption(self) -> None:
        row = self.pictures.currentRow()
        if row + 1 < self.pictures.count():
            self.pictures.setCurrentRow(row + 1)
            self.caption.setFocus()

    def _set_caption(self, text: str) -> None:
        item = self.pictures.currentItem()
        if item is not None:
            item.setData(Qt.UserRole + 1, text)
            self._label(self.pictures.row(item), item)
            self._redraw.start()

    def _label(self, row: int, item: QListWidgetItem) -> None:
        steps = self.layout_choice.value == "steps"
        caption = item.data(Qt.UserRole + 1) or ""
        item.setText(f"{row + 1}. {caption}" if steps else f"{row + 1}")
        item.setData(Qt.AccessibleTextRole, f"Snip {row + 1}" + (f": {caption}" if steps and caption else ""))

    def _changed(self) -> None:
        count = self.pictures.count()
        for row in range(count):
            self._label(row, self.pictures.item(row))
        steps = self.layout_choice.value == "steps"
        self.columns_row.setVisible(self.layout_choice.value == "grid")
        self.caption_row.setVisible(steps)
        for button in (self.remove_button, self.up_button, self.down_button):
            button.setEnabled(count > 0)
        self.add_button.setEnabled(count < combine.MAX_IMAGES)
        self.combine_button.setEnabled(2 <= count <= combine.MAX_IMAGES)
        self._redraw.start()

    def _show_preview(self) -> None:
        ids = self.ids()
        if len(ids) < 2:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("Add at least two snips to combine them.")
            self.size_label.clear()
            return
        sizes = [self._sizes[i] for i in ids]
        try:  # the real layout, from the pictures' sizes, drawn small
            drawn = combine.preview([self._previews[i] for i in ids], sizes, self.options(), PREVIEW_LONGEST)
            width, height = combine.measure(sizes, self.options())
        except ValueError as exc:
            self.preview.setPixmap(QPixmap())
            self.preview.setText(f"Can't combine these: {exc}")
            return
        pixmap = QPixmap.fromImage(drawn)
        self.preview.setText("")
        self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.size_label.setText(f"{width} × {height} pixels")

    def resizeEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(e)
        self._redraw.start()

    def combine_now(self) -> None:
        ids = self.ids()
        if not 2 <= len(ids) <= combine.MAX_IMAGES:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.combined = combine.combine([self._images[i] for i in ids], self.options())
        except ValueError as exc:
            self.note.setText(f"Can't combine these: {exc}")
            self.note.show()
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.accept()


def open_combine(library: Library, entry_ids: list[str], parent: QWidget | None = None) -> combine.Combined | None:
    """Show the Combine window; the combined image and its markup, or None if cancelled."""
    dialog = CombineDialog(library, entry_ids, parent)
    if parent is not None:
        dialog.setWindowModality(Qt.WindowModal)  # its window waits; a snip started meanwhile doesn't
    try:
        return dialog.combined if dialog.exec() == QDialog.Accepted else None
    finally:
        dialog.deleteLater()
