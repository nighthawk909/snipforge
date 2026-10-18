"""Settings > Capture's capture presets: the list, and the window that makes or changes one.

A preset's shortcut is registered (snipforge/shortcuts.py, RegisterHotKey) when the preset is
saved, so a combination another app has is refused there and then, with the reason.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import prefs, presets, scroll_input
from .presets import BEAUTIFY, BORDERS, MAX_NAME, MAX_PRESETS, MODE_NAMES, TORN, Preset
from .shortcut_edit import ShortcutEdit
from .shortcuts import label as shortcut_label
from .widgets import Segmented, SettingRow, ToggleSwitch, stack

DESTINATION_LABELS = {"editor": "Open the editor", "copy": "Copy", "save": "Save to a folder"}


class PresetDialog(QDialog):
    """Make or change one preset. `shortcuts` (snipforge.shortcuts.GlobalShortcuts) checks and
    registers its shortcut when it's saved; None where shortcuts of your own aren't available."""

    def __init__(self, preset: Preset, shortcuts=None, parent=None, new: bool = False) -> None:
        super().__init__(parent)
        self._shortcuts = shortcuts
        self._original = preset
        self.preset: Preset | None = None  # what was saved, once Save is clicked
        self._folder = preset.folder
        self.setWindowTitle("New preset" if new else "Change preset")
        self.setObjectName("PresetDialog")
        self.setMinimumWidth(640)
        col = QVBoxLayout(self)
        col.setContentsMargins(20, 18, 20, 16)
        col.setSpacing(4)
        self.name = QLineEdit(preset.name)
        self.name.setPlaceholderText("For example: Docs screenshot")
        self.name.setMaxLength(MAX_NAME)
        self.name.setAccessibleName("Name")
        self.name.setMinimumWidth(240)
        modes = [(key, MODE_NAMES[key]) for key in prefs.CAPTURE_MODES
                 if key != "scroll" or scroll_input.available() or preset.mode == "scroll"]
        self.mode = Segmented(modes, value=preset.mode if preset.mode in dict(modes) else "area")
        self.mode.setAccessibleName("Capture mode")
        self.mode.changed.connect(self._show_size)
        self.width_box, self.height_box = self._side(preset.fixed_size[0], "Width"), self._side(preset.fixed_size[1],
                                                                                               "Height")
        size = QWidget()
        srow = QHBoxLayout(size)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.addWidget(self.width_box)
        srow.addWidget(QLabel("×"))
        srow.addWidget(self.height_box)
        self.size_row = SettingRow("Size", "The frame's width and height, in screen pixels", size)
        self.delay = Segmented([(str(s), "None" if s == 0 else f"{s} s") for s in prefs.DELAYS],
                               value=str(preset.delay))
        self.delay.setAccessibleName("Delay")
        self.pointer = ToggleSwitch("Include the mouse pointer")
        self.pointer.setChecked(preset.pointer)
        self.beautify = Segmented(list(BEAUTIFY.items()), value=preset.beautify)
        self.beautify.setAccessibleName("Beautify")
        self.border = Segmented([(str(k), v) for k, v in BORDERS.items()], value=str(preset.border))
        self.border.setAccessibleName("Border")
        self.torn = Segmented(list(TORN.items()), value=preset.torn_key if preset.torn_key in TORN else "")
        self.torn.setAccessibleName("Torn edges")
        self.destination = Segmented(list(DESTINATION_LABELS.items()), value=preset.destination)
        self.destination.setAccessibleName("After the snip")
        self.destination.changed.connect(self._show_folder)
        folder_buttons = QWidget()
        frow = QHBoxLayout(folder_buttons)
        frow.setContentsMargins(0, 0, 0, 0)
        self.use_save_folder = QPushButton("Use the Save folder")
        self.use_save_folder.setProperty("variant", "ghost")
        self.use_save_folder.clicked.connect(lambda: self._set_folder(""))
        change = QPushButton("Change…")
        change.setAccessibleName("Change the folder")
        change.clicked.connect(self._choose_folder)
        frow.addWidget(self.use_save_folder)
        frow.addWidget(change)
        self.folder_row = SettingRow("Folder", "", folder_buttons)
        self.shortcut = ShortcutEdit()
        self.shortcut.setAccessibleName("Shortcut for this preset")
        self.shortcut.set_text(preset.shortcut)
        self.shortcut.editing.connect(self._shortcut_editing)
        shortcut_row = SettingRow("Shortcut", "Snips with this preset from anywhere (optional)", self.shortcut)
        shortcut_row.setVisible(shortcuts is not None and shortcuts.supported)
        self.problem = QLabel()
        self.problem.setProperty("sfRole", "error")
        self.problem.setWordWrap(True)
        self.problem.setTextFormat(Qt.PlainText)
        self.problem.hide()
        col.addWidget(stack(
            SettingRow("Name", "", self.name),
            SettingRow("Capture mode", "Full screen snips the monitor you're on at once", self.mode),
            self.size_row,
            SettingRow("Delay", "Count down before the screen freezes", self.delay),
            SettingRow("Include the mouse pointer", "", self.pointer),
            SettingRow("Beautify", "Backdrop, rounded corners and a shadow", self.beautify),
            SettingRow("Border", "", self.border),
            SettingRow("Torn edges", "", self.torn),
            SettingRow("After the snip", "Every snip is kept in the Library too", self.destination),
            self.folder_row,
            shortcut_row))
        col.addWidget(self.problem)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.save_button = QPushButton("Save")
        self.save_button.setProperty("variant", "primary")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_button)
        col.addSpacing(8)
        col.addLayout(buttons)
        self._show_size(self.mode.value)
        self._show_folder(self.destination.value)
        self.name.textChanged.connect(lambda _text: self.problem.hide())

    @staticmethod
    def _side(value: int, name: str) -> QSpinBox:
        box = QSpinBox()
        box.setRange(prefs.MIN_FIXED, prefs.MAX_FIXED)
        box.setValue(value)
        box.setAccessibleName(f"{name} in pixels")
        box.setCorrectionMode(QSpinBox.CorrectToNearestValue)
        return box

    def _show_size(self, mode: str) -> None:
        self.size_row.setVisible(mode == "fixed")
        self.adjustSize()

    def _show_folder(self, destination: str) -> None:
        self.folder_row.setVisible(destination == "save")
        self._set_folder(self._folder)

    def _set_folder(self, folder: str) -> None:
        self._folder = folder
        shown = folder or f"The Save folder in Settings ({prefs.export_dir()})"
        self.folder_row.set_description(shown)
        self.use_save_folder.setVisible(bool(folder))
        self.adjustSize()

    def _choose_folder(self) -> None:
        start = self._folder or str(prefs.export_dir())
        folder = QFileDialog.getExistingDirectory(self, "Save this preset's snips in", start)
        if folder:
            self._set_folder(str(Path(folder)))

    def _shortcut_editing(self, on: bool) -> None:
        """While a combination is typed, the other shortcuts step aside (as in Settings)."""
        if self._shortcuts is None:
            return
        if on:
            self._shortcuts.suspend()
        else:
            self._shortcuts.resume()

    def chosen(self) -> Preset:
        torn = presets.torn_sides(self.torn.value)
        for box in (self.width_box, self.height_box):
            box.interpretText()
        return replace(self._original, name=presets.clean_name(self.name.text()), mode=self.mode.value,
                       delay=int(self.delay.value), pointer=self.pointer.isChecked(),
                       fixed_size=(self.width_box.value(), self.height_box.value()), beautify=self.beautify.value,
                       border=int(self.border.value), torn=torn, destination=self.destination.value,
                       folder=self._folder if self.destination.value == "save" else "",
                       shortcut=self.shortcut.text())

    def save(self) -> None:
        preset = self.chosen()
        if not preset.name:
            self._say("Give the preset a name.")
            self.name.setFocus()
            return
        if self._shortcuts is not None and self._shortcuts.supported:
            problem = self._shortcuts.set(preset.action, preset.shortcut, title=preset.name)
            if problem:
                self._say(problem)
                return
            preset = replace(preset, shortcut=self._shortcuts.bindings().get(preset.action, ""))
        self.preset = preset
        self.accept()

    def _say(self, text: str) -> None:
        self.problem.setText(text)
        self.problem.show()


class PresetList(QWidget):
    """The presets, each with Change and Delete, and Add a preset."""

    changed = Signal()  # presets were added, changed or deleted

    def __init__(self, shortcuts=None, parent=None) -> None:
        super().__init__(parent)
        self._shortcuts = shortcuts
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 18, 0, 0)
        col.setSpacing(4)
        heading = QLabel("Capture presets")
        heading.setProperty("sfRole", "heading")
        about = QLabel("Saved ways to snip: a mode, a delay, the pointer, effects and where the snip goes. Pick one on "
                       "the capture toolbar (or press 1 to 9 there), in the tray menu, or with its own shortcut.")
        about.setProperty("sfRole", "muted")
        about.setWordWrap(True)
        col.addWidget(heading)
        col.addWidget(about)
        self._rows = QVBoxLayout()
        self._rows.setSpacing(4)
        col.addLayout(self._rows)
        self.add_button = QPushButton("Add a preset")
        self.add_button.clicked.connect(self.add)
        add_row = QHBoxLayout()
        add_row.addWidget(self.add_button)
        add_row.addStretch(1)
        col.addSpacing(4)
        col.addLayout(add_row)
        self.rows: dict[str, SettingRow] = {}
        self.refresh()

    def refresh(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.rows = {}
        saved = presets.load()
        for preset in saved:
            self.rows[preset.id] = row = SettingRow(preset.name, self._describe(preset), self._buttons(preset))
            self._rows.addWidget(row)
        self.add_button.setEnabled(len(saved) < MAX_PRESETS)
        self.add_button.setToolTip("" if len(saved) < MAX_PRESETS else f"Up to {MAX_PRESETS} presets")

    def _describe(self, preset: Preset) -> str:
        text = preset.summary()
        bound = self._shortcuts.bindings().get(preset.action) if self._shortcuts is not None else None
        if bound:
            text += f" · {shortcut_label(bound)}"
        elif preset.shortcut and self._shortcuts is not None and self._shortcuts.supported:
            problem = self._shortcuts.problems.get(preset.action, "another app has it")
            text += f" · {shortcut_label(preset.shortcut)} isn't working: {problem}"
        return text

    def _buttons(self, preset: Preset) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        change = QPushButton("Change…")
        change.setAccessibleName(f"Change {preset.name}")
        change.clicked.connect(lambda: self.change(preset.id))
        delete = QPushButton("Delete")
        delete.setProperty("variant", "ghost")
        delete.setAccessibleName(f"Delete {preset.name}")
        delete.clicked.connect(lambda: self.delete(preset.id))
        row.addWidget(change)
        row.addWidget(delete)
        return box

    def _dialog(self, preset: Preset, new: bool) -> Preset | None:
        dialog = PresetDialog(preset, self._shortcuts, self.window(), new=new)
        return dialog.preset if dialog.exec() == QDialog.Accepted else None

    def add(self) -> None:
        saved = presets.load()
        if len(saved) >= MAX_PRESETS:
            return
        made = self._dialog(Preset(presets.new_id(saved), "", mode=prefs.capture_mode(),
                                   fixed_size=prefs.capture_fixed_size()), new=True)
        if made is not None:
            self._save([*presets.load(), made])

    def change(self, preset_id: str) -> None:
        current = presets.get(preset_id)
        if current is None:
            return
        made = self._dialog(current, new=False)
        if made is not None:
            self._save([made if p.id == preset_id else p for p in presets.load()])

    def delete(self, preset_id: str) -> None:
        current = presets.get(preset_id)
        if current is None:
            return
        answer = QMessageBox.question(self.window(), "Delete preset", f"Delete the preset “{current.name}”?",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        if self._shortcuts is not None:
            self._shortcuts.forget(current.action)
        self._save([p for p in presets.load() if p.id != preset_id])

    def _save(self, updated: list[Preset]) -> None:
        presets.save(updated)
        self.refresh()
        self.changed.emit()
