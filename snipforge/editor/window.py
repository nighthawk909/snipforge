"""Editor window shown right after a snip is taken (or reopened from the library).

Layout: command bar on top, tool rail on the left, the snip in the middle, the properties panel on
the right and recent snips along the bottom.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QImage, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import ocr, redact, theme
from ..dragout import DragOutButton
from ..icons import icon
from ..library import Entry, Library
from ..settings import app_settings
from .beautify import apply_beautify
from .canvas import MAX_ZOOM, MIN_ZOOM, Canvas
from .chrome import RECENT_COUNT, ZOOM_STEP, CommandBar, Tray
from .panel import PropertiesPanel
from .share_menu import ShareController
from .style import Style
from .textdialog import GrabbedTextDialog

log = logging.getLogger(__name__)

TOOLS = [
    ("select", "Select and move (V)"),
    None,
    ("arrow", "Arrow (A)"), ("line", "Line (L)"), ("rect", "Rectangle (R)"), ("ellipse", "Ellipse (E)"),
    None,
    ("text", "Text (T)"), ("callout", "Callout: text in a bubble or box (K)"), ("stamp", "Stamp / icon (I)"),
    ("step", "Step number (N)"), ("pen", "Pen (P)"), ("highlight", "Highlighter (H)"),
    None,
    ("blur", "Blur (B)"), ("pixelate", "Pixelate (X)"), ("spotlight", "Spotlight (S)"),
    None,
    ("crop", "Crop (C)"),
]
EXPORT_DIR = Path.home() / "Pictures" / "SnipForge"
AUTOSAVE_MS = 700


def load_tool_styles(settings: QSettings) -> dict[str, Style]:
    raw = settings.value("tool_styles", "")
    try:
        data = json.loads(raw) if raw else {}
        return {k: Style.from_dict(v) for k, v in data.items()}
    except (TypeError, ValueError):
        return {}


class EditorWindow(QMainWindow):
    def __init__(self, image: QImage, library: Library, entry: Entry | None, document: dict | None = None,
                 on_new_snip=None, on_open_entry=None, on_pin=None, autosave: bool = True,
                 on_open_library=None) -> None:
        super().__init__()
        self._library, self._entry = library, entry
        self._autosave_enabled = autosave and entry is not None
        self._fitted = False
        self._on_new_snip, self._on_open_entry, self._on_pin = on_new_snip, on_open_entry, on_pin
        self._on_open_library = on_open_library
        self._settings = app_settings()
        self._export_path: Path | None = None
        self._beautify = self._settings.value("beautify", False, bool)

        self.canvas = Canvas(image, load_tool_styles(self._settings))
        if document:
            self.canvas.load_document(document)
        self.commands = CommandBar()
        self.panel = PropertiesPanel()
        self.tray = Tray()
        self._tool_actions: dict[str, QAction] = {}

        root = QWidget()
        root.setObjectName("Root")
        column = QVBoxLayout(root)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.commands)
        middle = QHBoxLayout()
        middle.setSpacing(0)
        middle.addWidget(self._build_tool_rail())
        middle.addWidget(self.canvas, 1)
        middle.addWidget(self.panel)
        column.addLayout(middle, 1)
        column.addWidget(self.tray)
        self.setCentralWidget(root)
        self._wire_commands()
        self._build_shortcuts()

        self._autosave = QTimer(self, singleShot=True, interval=AUTOSAVE_MS)
        self._autosave.timeout.connect(self._save_to_library)
        self.canvas.document_changed.connect(self._autosave.start)
        self.canvas.document_changed.connect(self._refresh_size)
        self.canvas.document_changed.connect(self._mark_unsaved)
        self.canvas.selection_changed.connect(self._refresh_props)
        self.canvas.tool_changed.connect(lambda t: self._tool_actions[t].setChecked(True))
        self.canvas.zoom_changed.connect(self.commands.set_zoom)
        # Straight to the buttons (not a Python slot) so nothing runs while the stack is torn down.
        self.canvas.undo_stack.canUndoChanged.connect(self.commands.undo_button.setEnabled)
        self.canvas.undo_stack.canRedoChanged.connect(self.commands.redo_button.setEnabled)
        self.panel.changed.connect(lambda changes: self.canvas.apply_style(**changes))
        self.panel.crop_action.connect(self._crop_action)
        self.panel.editing_done.connect(self.canvas.setFocus)
        self.tray.beautify_changed.connect(self._set_beautify)
        self.tray.set_beautify(self._beautify)
        self.tray.open_entry.connect(self._open_entry)
        self.tray.open_library.connect(self._open_library)
        self.tray.library_button.setVisible(self._on_open_library is not None)
        if self._autosave_enabled:
            self.tray.set_saved("Saved", "Changes save to your library automatically")
        elif entry is not None:
            self.tray.set_saved("Autosave off", "Use Save or Copy to keep what you change")

        self._update_title()
        self._refresh_props()
        self._refresh_size()
        self._refresh_history()
        self._fit_to_screen(image)
        self._refresh_recent()
        self._retheme()
        theme.on_change(self._retheme)

    # ------------------------------------------------------------------ layout
    def _build_tool_rail(self) -> QToolBar:
        rail = QToolBar("Tools")
        rail.setObjectName("ToolRail")
        rail.setOrientation(Qt.Vertical)
        rail.setIconSize(QSize(20, 20))
        rail.setMovable(False)
        group = QActionGroup(self)
        for spec in TOOLS:
            if spec is None:
                rail.addSeparator()
                continue
            name, tip = spec
            act = QAction(tip, self)
            act.setCheckable(True)
            act.triggered.connect(lambda _=False, n=name: self._choose_tool(n))
            group.addAction(act)
            rail.addAction(act)
            self._tool_actions[name] = act
        self._tool_actions["select"].setChecked(True)
        return rail

    def _wire_commands(self) -> None:
        bar = self.commands
        bar.new_button.setVisible(self._on_new_snip is not None)
        if self._on_new_snip:
            bar.new_capture.connect(self._on_new_snip)
        bar.undo.connect(self.canvas.undo_stack.undo)
        bar.redo.connect(self.canvas.undo_stack.redo)
        bar.zoom_in.connect(self._zoom_in)
        bar.zoom_out.connect(self._zoom_out)
        bar.fit.connect(self.canvas.fit_image)
        bar.grab_text.connect(self.grab_text)
        bar.redact.connect(self.auto_redact)
        bar.pin.connect(self.pin)
        bar.save.connect(self.save)
        bar.copy.connect(self.copy_to_clipboard)
        bar.toggle_theme.connect(self._toggle_theme)
        bar.theme_button.setVisible(theme.get_manager() is not None)
        bar.insert_before_share(DragOutButton(self.final_image), "drag")
        bar.share_button.setMenu(self._build_share_menu(bar.share_button))

    def statusBar(self) -> QStatusBar:  # noqa: N802 - Qt's name; messages show in the tray
        return self.tray.status

    def _build_shortcuts(self) -> None:
        def add(shortcut, slot):
            act = QAction(self)
            act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(slot)
            self.addAction(act)

        add("Ctrl+C", self.copy_to_clipboard)
        add("Ctrl+S", self.save)
        add("Ctrl+Shift+S", self.save_as)
        add("Ctrl+Z", self.canvas.undo_stack.undo)
        add("Ctrl+Y", self.canvas.undo_stack.redo)
        add("Ctrl+Shift+Z", self.canvas.undo_stack.redo)
        add("Ctrl+D", self.canvas.duplicate_selection)
        add("Ctrl+]", lambda: self.canvas.reorder_selection(True))
        add("Ctrl+[", lambda: self.canvas.reorder_selection(False))
        add("Ctrl+A", self.canvas.select_all)
        add("Ctrl+E", self.share_email)
        add("Ctrl+0", self.canvas.fit_image)
        add("Ctrl+1", lambda: self.canvas.set_zoom(1.0))
        add("Ctrl+=", self._zoom_in)
        add("Ctrl+-", self._zoom_out)
        add("Ctrl+P", self.pin)
        add("Ctrl+Shift+T", self.grab_text)
        add("Ctrl+W", self.close)
        if self._on_new_snip:
            add("Ctrl+N", self._on_new_snip)

    def _fit_to_screen(self, image: QImage) -> None:
        avail = QGuiApplication.primaryScreen().availableGeometry()
        w = min(max(image.width() + 420, 1100), int(avail.width() * 0.92))
        h = min(max(image.height() + 200, 640), int(avail.height() * 0.92))
        self.resize(w, h)
        self.move(avail.center().x() - w // 2, avail.center().y() - h // 2)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._fitted:  # only on first show, so restoring from minimize keeps your zoom
            self._fitted = True
            QTimer.singleShot(0, self, self._fit_once)  # dropped if the window is gone by then
        self.canvas.setFocus()

    def _fit_once(self) -> None:
        if not self.isVisible():
            return
        self.canvas.fit_image()
        avail = self.screen().availableGeometry()
        frame = self.frameGeometry()  # the real size, after minimum sizes were applied
        frame.moveCenter(avail.center())
        self.move(max(avail.left(), frame.left()), max(avail.top(), frame.top()))

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._refresh_recent()

    def _retheme(self, _theme=None) -> None:
        for name, act in self._tool_actions.items():
            act.setIcon(icon(name))
        self.commands.retheme()
        self.panel.retheme()

    # ------------------------------------------------------------------ state sync
    def _choose_tool(self, name: str) -> None:
        self.canvas.set_tool(name)
        self.canvas.setFocus()

    def _zoom_in(self) -> None:
        self.canvas.set_zoom(min(MAX_ZOOM, self.canvas.zoom() * ZOOM_STEP))

    def _zoom_out(self) -> None:
        self.canvas.set_zoom(max(MIN_ZOOM, self.canvas.zoom() / ZOOM_STEP))

    def _toggle_theme(self) -> None:
        manager = theme.get_manager()
        if manager is not None:
            manager.set_mode_preference("light" if manager.theme.dark else "dark")

    def _set_beautify(self, on: bool) -> None:
        self._beautify = on
        self._settings.setValue("beautify", on)

    def _open_entry(self, entry_id: str) -> None:
        if self._on_open_entry:
            self._on_open_entry(entry_id)

    def _open_library(self) -> None:
        if self._on_open_library:
            self._on_open_library()

    def _crop_action(self, action: str) -> None:
        if action == "reset":
            self.canvas.reset_crop_draft()
        else:
            if action == "cancel":
                self.canvas.cancel_crop()
            self.canvas.set_tool("select")  # leaving Crop applies whatever is left pending
        self.canvas.setFocus()

    def _refresh_props(self) -> None:
        kind, style = self.canvas.style_target()
        self.panel.load(kind, style, bool(self.canvas.selected_annotations()))

    def _mark_unsaved(self) -> None:
        if self._autosave_enabled:
            self.tray.set_saved("Saving…")

    def _refresh_size(self) -> None:
        size = self.canvas.crop_rect().toAlignedRect().size()
        self.panel.set_image_size(size.width(), size.height())
        self.tray.set_image_size(size.width(), size.height())

    def _refresh_history(self) -> None:
        stack = self.canvas.undo_stack
        self.commands.set_history(stack.canUndo(), stack.canRedo())

    def _refresh_recent(self) -> None:
        current = self._entry.id if self._entry else None
        entries = [e for e in self._library.entries(RECENT_COUNT + 1) if e.id != current][:RECENT_COUNT]
        self.tray.set_recent(entries)

    def _update_title(self) -> None:
        when = self._entry.created.strftime("%b %d, %I:%M %p") if self._entry else "Unsaved"
        self.setWindowTitle(f"SnipForge — {when}")

    # ------------------------------------------------------------------ output
    @property
    def export_path(self) -> Path | None:
        return self._export_path

    def _build_share_menu(self, parent):
        self._share = ShareController(self)
        return self._share.build_menu(parent)

    def share_email(self) -> None:
        self._share.email()

    def final_image(self) -> QImage:
        image = self.canvas.render_image()
        return apply_beautify(image) if self._beautify else image

    def copy_to_clipboard(self) -> None:
        editing = self.canvas.editing_text_item()
        if editing is not None:  # Ctrl+C while typing copies the selected text, not the image
            selected = editing.textCursor().selectedText()
            if selected:
                QGuiApplication.clipboard().setText(selected)
            return
        QGuiApplication.clipboard().setImage(self.final_image())
        self.statusBar().showMessage("Copied to clipboard", 2500)

    def save(self) -> None:
        if self._export_path is None:
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            self._export_path = EXPORT_DIR / dt.datetime.now().strftime("snip-%Y%m%d-%H%M%S.png")
        self._write(self._export_path)

    def save_as(self) -> None:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        start = self._export_path or EXPORT_DIR / dt.datetime.now().strftime("snip-%Y%m%d-%H%M%S.png")
        path, _ = QFileDialog.getSaveFileName(self, "Save snip", str(start),
                                              "PNG (*.png);;JPEG (*.jpg *.jpeg);;Bitmap (*.bmp)")
        if path:
            self._export_path = Path(path)
            self._write(self._export_path)

    def _write(self, path: Path) -> None:
        if self.final_image().save(str(path)):
            self.statusBar().showMessage(f"Saved {path}", 4000)
        else:
            QMessageBox.warning(self, "SnipForge", f"Couldn't save to {path}. Check the folder exists "
                                "and you have permission to write there.")

    def pin(self) -> None:
        if self._on_pin:
            self._on_pin(self.final_image())

    # ------------------------------------------------------------------ OCR
    def _ocr_source(self) -> tuple[QImage, float, float]:
        crop = self.canvas.crop_rect().toAlignedRect()
        return self.canvas.base_image.copy(crop), crop.x(), crop.y()

    def _run_ocr(self):
        image, _, _ = self._ocr_source()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return ocr.recognize(image)
        except ocr.OcrUnavailable as exc:
            QMessageBox.information(self, "SnipForge", str(exc))
        except Exception as exc:  # WinRT raises plain OSError/RuntimeError variants
            log.exception("OCR failed")
            QMessageBox.warning(self, "SnipForge", f"Text recognition failed: {exc}")
        finally:
            QApplication.restoreOverrideCursor()
        return None

    def grab_text(self) -> None:
        result = self._run_ocr()
        if result is None:
            return
        if result.text:
            QGuiApplication.clipboard().setText(result.text)
        GrabbedTextDialog(result.text, self).exec()

    def auto_redact(self) -> None:
        result = self._run_ocr()
        if result is None:
            return
        findings = redact.find_sensitive(result.lines)
        if not findings:
            self.statusBar().showMessage("Nothing sensitive found", 3500)
            return
        _, ox, oy = self._ocr_source()
        count = self.canvas.add_redactions([f.rect.translated(ox, oy) for f in findings], mode="blur")
        self.statusBar().showMessage(f"Blurred {count}: {redact.summarize(findings)}. Ctrl+Z to undo.", 6000)

    # ------------------------------------------------------------------ persistence
    def _save_to_library(self) -> None:
        if not self._autosave_enabled:
            return
        if self.canvas.editing_text_item() is not None and not self.canvas.closing:
            self._autosave.start()  # don't interrupt typing; try again shortly
            return
        try:
            self._library.save(self._entry, self.canvas.to_document(),
                               self.canvas.render_image(commit=False, use_draft=False))
        except OSError as exc:
            log.warning("Autosave failed: %s", exc)
            self.tray.set_saved("Not saved", f"Couldn't autosave: {exc}")
            self.statusBar().showMessage(f"Couldn't autosave: {exc}", 5000)
            return
        self.tray.set_saved("Saved", dt.datetime.now().strftime("Saved to your library at %I:%M %p"))

    def closeEvent(self, e) -> None:
        self.canvas.commit_text()
        self.canvas.apply_crop()
        self._autosave.stop()
        self.canvas.closing = True
        self._save_to_library()
        styles = {k: v.to_dict() for k, v in self.canvas.tool_styles.items()}
        self._settings.setValue("tool_styles", json.dumps(styles))
        super().closeEvent(e)
