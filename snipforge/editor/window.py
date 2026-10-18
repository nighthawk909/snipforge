"""Editor window shown right after a snip is taken (or reopened from the library).

Layout: command bar on top, tool rail on the left, the snip in the middle, the properties panel on
the right and recent snips along the bottom.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QImage, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import ocr, prefs, redact, theme
from ..dragout import DragOutButton
from ..icons import icon
from ..library import Entry, Library
from ..settings import app_settings
from ..wording import keys
from . import beautify
from .app_stamp import stamp_parts
from .beautify import DEFAULT_STYLE, apply_beautify
from .canvas import MAX_ZOOM, MIN_ZOOM, Canvas, UnsupportedDocument
from .canvas_clipboard import MARKUP_MIME
from .chrome import RECENT_COUNT, ZOOM_STEP, CommandBar, Tray
from .export import SAVE_FILTERS, ExportError, filter_for, save_image, with_suffix
from .export import fits as export_fits
from .panel import PropertiesPanel
from .quick_tiles import TOOL_NAMES
from .resize_dialog import ResizeDialog
from .share_menu import ShareController
from .style import Style
from .textdialog import GrabbedTextDialog
from .tool_rail import ToolRail

log = logging.getLogger(__name__)

TOOLS = [
    ("select", "Select and move (V)"),
    None,
    ("arrow", "Arrow (A)"), ("line", "Line (L)"), ("rect", "Rectangle (R)"), ("ellipse", "Ellipse (E)"),
    ("spotlight", "Spotlight (S)"), ("magnify", "Magnify: enlarge part of the snip (M)"),
    None,
    ("text", "Text (T)"), ("callout", "Callout: text in a bubble or box (K)"), ("stamp", "Stamp / icon (I)"),
    ("step", "Step number (N)"), ("pen", "Pen (P)"), ("highlight", "Highlighter (H)"),
    None,
    # Groups that fill whole rows of 3 keep the rail's 3-column layout short enough for a 1080p screen
    # at 200% scaling (tests/test_tool_rail.py).
    ("blur", "Blur (B)"), ("pixelate", "Pixelate (X)"), ("erase", "Smart erase: fill an area from around it (W)"),
    None,
    ("crop", "Crop (C)"), ("cutout", "Cut out: remove a strip and join the rest (U)"),
    ("move", "Smart Move: drag out part of the snip, then put it anywhere; where it was is filled in (G)"),
]
AUTOSAVE_MS = 700
META_TRIES, META_RETRY_S = 4, 0.05  # reading a snip's details: tries, and the first wait between them
PICK_HINT = ("Click anywhere on the snip to pick its color, or move with the arrow keys and press Enter. "
             "Esc cancels.")
CHOOSE_HINT = "Click the line of text to change. Esc cancels."


def load_tool_styles(settings: QSettings) -> dict[str, Style]:
    raw = settings.value("tool_styles", "")
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw else {}
        return {k: Style.from_dict(v) for k, v in data.items() if isinstance(v, dict)}
    except (TypeError, ValueError, AttributeError, RecursionError):
        return {}


class EditorWindow(QMainWindow):
    def __init__(self, image: QImage, library: Library, entry: Entry | None, document: dict | None = None,
                 on_new_snip=None, on_open_entry=None, on_pin=None, autosave: bool = True,
                 on_open_library=None, beautify_on: bool | None = None, on_combine=None) -> None:
        """`beautify_on`: start with Beautify on or off (a capture preset's choice) instead of
        following the switch in Settings; the switch still changes it."""
        super().__init__()
        self._library, self._entry = library, entry
        self._autosave_enabled = autosave and entry is not None
        self._fitted = False
        self._on_new_snip, self._on_open_entry, self._on_pin = on_new_snip, on_open_entry, on_pin
        self._on_open_library = on_open_library
        self._on_combine = on_combine  # (this snip's id, this window): put it together with others
        self._settings = app_settings()
        self._export_path: Path | None = None
        self._beautify = beautify.enabled() if beautify_on is None else beautify_on

        self.canvas = Canvas(image, load_tool_styles(self._settings))
        unsupported = False
        if document:
            try:
                self.canvas.load_document(document)
            except UnsupportedDocument as exc:
                log.warning("Opening snip without its markup: %s", exc)
                self._autosave_enabled, unsupported = False, True
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
        self.tool_rail = self._build_tool_rail()
        middle.addWidget(self.tool_rail)
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
        self.panel.image_action.connect(self.image_action)
        self.panel.pick_color.connect(self._toggle_picking)
        self.panel.edges_changed.connect(self.canvas.set_edges)
        self.canvas.notice.connect(lambda text: self.statusBar().showMessage(text, 6000))
        self.canvas.copy_requested.connect(self.copy_to_clipboard)
        self.panel.source_stamp.connect(self.add_source_stamp)
        self.panel.set_source_stamp_available(entry is not None)
        self.panel.cutout_changed.connect(lambda changes: self.canvas.set_cutout_options(**changes))
        self.panel.style_saved.connect(
            lambda kind: self.statusBar().showMessage(f"Saved as a quick style for {TOOL_NAMES.get(kind, kind)}", 3000))
        self.canvas.picking_changed.connect(self._show_picking)
        self.canvas.choosing_changed.connect(self._show_choosing)
        self.canvas.color_picked.connect(
            lambda color: self.statusBar().showMessage(f"Picked {color.upper()}", 3000))
        self.panel.editing_done.connect(self.canvas.setFocus)
        self.tray.beautify_changed.connect(beautify.set_enabled)
        beautify.switch().changed.connect(self._show_beautify)  # from any editor or Settings
        self.tray.set_beautify(self._beautify)
        self.canvas.set_beautify(DEFAULT_STYLE if self._beautify else None)
        self.tray.open_entry.connect(self._open_entry)
        self.tray.open_library.connect(self._open_library)
        self.tray.library_button.setVisible(self._on_open_library is not None)
        if self._autosave_enabled:
            self.tray.set_saved("Saved", "Changes save to your library automatically")
        elif entry is not None:
            self.tray.set_saved("Autosave off", "Use Save or Copy to keep what you change")
        if unsupported:
            self.tray.set_saved("Autosave off", "This snip's markup was saved by a newer SnipForge. It opens without "
                                "it, and autosave is off so the saved file isn't changed.")
            self.statusBar().showMessage("Saved by a newer SnipForge: opened without its markup, and autosave is off",
                                         10000)

        self._update_title()
        self._refresh_props()
        self._refresh_size()
        self._refresh_history()
        self._fit_to_screen(image)
        self._refresh_recent()
        self._retheme()
        theme.on_change(self._retheme)

    # ------------------------------------------------------------------ layout
    def _build_tool_rail(self) -> ToolRail:
        group = QActionGroup(self)
        groups: list[list[tuple[str, QAction]]] = [[]]
        for spec in TOOLS:
            if spec is None:
                groups.append([])
                continue
            name, tip = spec
            act = QAction(tip, self)
            act.setCheckable(True)
            act.triggered.connect(lambda _=False, n=name: self._choose_tool(n))
            group.addAction(act)
            groups[-1].append((name, act))
            self._tool_actions[name] = act
        self._tool_actions["select"].setChecked(True)
        return ToolRail(groups)

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
        bar.edit_text.connect(self.edit_text)
        bar.redact.connect(self.auto_redact)
        bar.pin.connect(self.pin)
        bar.combine.connect(self.combine)
        # Only a snip that saves to the Library: what's combined is the Library's copy of it
        bar.combine_button.setVisible(self._on_combine is not None and self._autosave_enabled)
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
        add("Ctrl+Shift+E", self.edit_text)
        add("Ctrl+G", self.combine)
        add("Ctrl+X", self.cut)
        add("Ctrl+V", self.paste)
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
        add("Ctrl+R", lambda: self.image_action("rotate_right"))
        add("Ctrl+Shift+R", lambda: self.image_action("rotate_left"))
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

    def _show_beautify(self, on: bool) -> None:
        self.tray.set_beautify(on)
        if on != self._beautify:
            self._beautify = on
            self.canvas.set_beautify(DEFAULT_STYLE if on else None)

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

    def _show_picking(self, on: bool) -> None:
        self.panel.set_picking(on)
        if not on and self.statusBar().currentMessage() == PICK_HINT:
            self.statusBar().clearMessage()

    def _show_choosing(self, on: bool) -> None:
        if not on and self.statusBar().currentMessage() == CHOOSE_HINT:
            self.statusBar().clearMessage()

    def add_source_stamp(self) -> None:
        """The App name and date stamp, from what the Library recorded when the snip was taken."""
        if self._entry is None:
            return
        meta = self._read_meta()
        where, when = stamp_parts(meta.title if meta else "", meta.app if meta else "", self._entry.created)
        self.canvas.add_text_stamp(where, when)
        if meta is None:
            message = ("Added when you took this snip. Where it came from couldn't be read just now: delete the "
                       "stamp and try again in a moment.")
        elif where:
            message = "Added where and when you took this snip. It's text: edit or move it."
        else:
            message = "Added when you took this snip (no app was recorded)"
        self.statusBar().showMessage(message, 8000 if meta is None else 5000)
        self.canvas.setFocus()

    def _read_meta(self):
        """The Library's details for this snip. The search indexer or antivirus can hold meta.json for a
        moment, so a failed read is tried again briefly; None if it still can't be read."""
        for attempt in range(META_TRIES):
            try:
                return self._library.read_meta(self._entry)
            except OSError:
                if attempt < META_TRIES - 1:
                    time.sleep(META_RETRY_S * (attempt + 1))
        return None

    def _toggle_picking(self) -> None:
        if self.canvas.picking():
            self.canvas.stop_picking()
        else:
            self.canvas.start_picking()
            self.statusBar().showMessage(PICK_HINT, 6000)
        self.canvas.setFocus()

    def image_action(self, action: str) -> None:
        """Turn, flip or resize the snip (the panel's Image buttons and Ctrl+R)."""
        canvas = self.canvas
        if not canvas.can_change_image():
            self.statusBar().showMessage("This snip has markup from a newer SnipForge, so it can't be turned, "
                                         "flipped or resized here", 6000)
        elif action == "resize":
            self._resize()
        elif action in ("rotate_left", "rotate_right"):
            canvas.rotate_image(clockwise=action == "rotate_right")
        elif action in ("flip_horizontal", "flip_vertical"):
            canvas.flip_image(horizontal=action == "flip_horizontal")
        canvas.setFocus()

    def _resize(self) -> None:
        self.canvas.commit_text()
        if self.canvas.tool == "crop":
            self.canvas.set_tool("select")  # resize what you'll export, with the crop applied
        size = self.canvas.crop_rect().toAlignedRect().size()
        dialog = ResizeDialog(size.width(), size.height(), self.canvas.can_resize, self)
        if dialog.exec():
            try:
                self.canvas.resize_image(*dialog.result_size())
            except ValueError as exc:
                QMessageBox.warning(self, "SnipForge", f"Couldn't resize the snip: {exc}")

    def _refresh_props(self) -> None:
        kind, style = self.canvas.style_target()
        self.panel.load(kind, style, bool(self.canvas.selected_annotations()))
        self.panel.load_edges(self.canvas.edges())
        self.panel.load_cutout(self.canvas.cutout_options)

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

    def combine(self) -> None:
        """Combine this snip with others: it's saved first, so the combined image shows it as it is now."""
        if self._on_combine is None or not self._autosave_enabled:
            return
        self.canvas.commit_text()
        self._autosave.stop()
        if not self._save_to_library():
            self.statusBar().showMessage("Couldn't save this snip, so it can't be combined yet. Try again in a "
                                         "moment.", 6000)
            return
        self._on_combine(self._entry.id, self)

    def final_image(self) -> QImage:
        image = self.canvas.render_image()
        return apply_beautify(image, DEFAULT_STYLE) if self._beautify else image

    def copy_to_clipboard(self) -> None:
        editing = self.canvas.editing_text_item()
        if editing is not None:  # Ctrl+C while typing copies the selected text, not the image
            selected = editing.textCursor().selectedText()
            if selected:
                QGuiApplication.clipboard().setText(selected)
            return
        mime = self.canvas.copy_mime(self.final_image())  # the snip, plus the selected markup for Paste
        with_markup = mime.hasFormat(MARKUP_MIME)
        QGuiApplication.clipboard().setMimeData(mime)
        message = "Copied to clipboard with Beautify" if self._beautify else "Copied to clipboard"
        if with_markup:
            message += f". {keys('Ctrl+V')} in a snip pastes the selected markup"
        self.statusBar().showMessage(message, 5000 if with_markup else 2500)

    def cut(self) -> None:
        """Ctrl+X: the selected markup. (While you type, the text takes Ctrl+X, Ctrl+C and Ctrl+V itself.)"""
        if self.canvas.cut_selection():
            self.statusBar().showMessage(f"Cut. {keys('Ctrl+V')} in a snip pastes it", 4000)

    def paste(self) -> None:
        """Ctrl+V: markup copied in any snip, or text as a text box."""
        self.canvas.paste()

    @staticmethod
    def _new_export_path() -> Path:
        """In the Save folder chosen in Settings (Pictures\\SnipForge by default), in its format."""
        folder = prefs.export_dir()
        folder.mkdir(parents=True, exist_ok=True)
        return folder / dt.datetime.now().strftime(f"snip-%Y%m%d-%H%M%S.{prefs.export_format()}")

    def save(self) -> None:
        if self._export_path is None:
            try:
                path = self._new_export_path()
            except OSError as exc:
                QMessageBox.warning(self, "SnipForge", f"Couldn't use the save folder: {exc}")
                return
            note = ""
            if not export_fits(self.final_image(), path):  # too big for WebP: Save still saves, as PNG
                path, note = path.with_suffix(".png"), "It's too big for WebP, so it's a PNG."
            self._export_path = path
            self._write(path, note)
            return
        self._write(self._export_path)

    def save_as(self) -> None:
        try:
            start = self._export_path or self._new_export_path()
        except OSError:
            start = Path.home() / "Pictures"
        path, chosen = QFileDialog.getSaveFileName(self, "Save snip", str(start), SAVE_FILTERS, filter_for(start))
        if path:
            self._export_path = with_suffix(path, chosen)
            self._write(self._export_path)

    def _write(self, path: Path, note: str = "") -> None:
        reason = ""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            ok = save_image(self.final_image(), path)
        except ExportError as exc:
            ok, reason = False, str(exc)
        except OSError:
            ok = False
        if ok:
            self.statusBar().showMessage(f"Saved {path}. {note}" if note else f"Saved {path}", 6000 if note else 4000)
            return
        if path == self._export_path:
            self._export_path = None  # try the Save folder from Settings again next time
        QMessageBox.warning(self, "SnipForge", reason or f"Couldn't save to {path}. Check the folder exists and you "
                            "have permission to write there, or choose another in Settings > Saving.")

    def pin(self) -> None:
        if self._on_pin:
            self._on_pin(self.final_image())

    # ------------------------------------------------------------------ OCR
    def _run_ocr(self) -> tuple[ocr.OcrResult | None, QPointF]:
        """The text in what's kept of the snip, and where that part starts on the snip (OCR's
        rectangles are relative to it)."""
        crop = self.canvas.crop_rect().toAlignedRect()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return ocr.recognize(self.canvas.content_image().copy(crop)), QPointF(crop.topLeft())
        except ocr.OcrUnavailable as exc:
            QMessageBox.information(self, "SnipForge", str(exc))
        except Exception as exc:  # WinRT raises plain OSError/RuntimeError variants
            log.exception("OCR failed")
            QMessageBox.warning(self, "SnipForge", f"Text recognition failed: {exc}")
        finally:
            QApplication.restoreOverrideCursor()
        return None, QPointF()

    def grab_text(self) -> None:
        result, _ = self._run_ocr()
        if result is None:
            return
        if result.text:
            QGuiApplication.clipboard().setText(result.text)
        GrabbedTextDialog(result.text, self).exec()

    def edit_text(self) -> None:
        """Edit text: find the snip's lines of text, let you click one, ask for its new words, then
        smart-erase it and write them over it in a matching size and colour."""
        from .edit_text import line_rect

        result, origin = self._run_ocr()
        if result is None:
            return
        lines = [line for line in result.lines if line.words and line.text.strip()]
        if not lines:
            self.statusBar().showMessage("No text found in this snip", 3500)
            return
        rects = [line_rect(line).translated(origin) for line in lines]
        self.canvas.setFocus()
        self.canvas.start_choosing(rects, lambda n: self._retype(rects[n], lines[n].text))
        self.statusBar().showMessage(CHOOSE_HINT, 0)

    def _retype(self, rect, old: str) -> None:
        new, ok = QInputDialog.getText(self, "Edit text", "Change it to (leave it empty to remove the line):",
                                       QLineEdit.Normal, old)
        if not ok or new == old:
            return
        self.canvas.edit_line(rect, old, new)
        self.statusBar().showMessage("Changed. It's a text box now: select it to change it again. "
                                     f"{keys('Ctrl+Z')} undoes it.", 6000)

    def auto_redact(self) -> None:
        result, origin = self._run_ocr()
        if result is None:
            return
        findings = redact.find_sensitive(result.lines)
        if not findings:
            self.statusBar().showMessage("Nothing sensitive found", 3500)
            return
        count = self.canvas.add_redactions([f.rect.translated(origin) for f in findings], mode="blur")
        self.statusBar().showMessage(f"Blurred {count}: {redact.summarize(findings)}. {keys('Ctrl+Z')} to undo.",
                                     6000)

    # ------------------------------------------------------------------ persistence
    def _save_to_library(self) -> bool:
        """Autosave. False if it couldn't be saved now (autosave is off, typing, or a write failed)."""
        if not self._autosave_enabled:
            return False
        if self.canvas.editing_text_item() is not None and not self.canvas.closing:
            self._autosave.start()  # don't interrupt typing; try again shortly
            return False
        try:
            self._library.save(self._entry, self.canvas.to_document(),
                               self.canvas.render_image(commit=False, use_draft=False))
        except OSError as exc:
            log.warning("Autosave failed: %s", exc)
            self.tray.set_saved("Not saved", f"Couldn't autosave: {exc}")
            self.statusBar().showMessage(f"Couldn't autosave: {exc}", 5000)
            return False
        self.tray.set_saved("Saved", dt.datetime.now().strftime("Saved to your library at %I:%M %p"))
        return True

    def closeEvent(self, e) -> None:
        self.canvas.stop_picking()
        self.canvas.commit_text()
        self.canvas.apply_crop()
        self._autosave.stop()
        self.canvas.closing = True
        self._save_to_library()
        styles = {k: v.to_dict() for k, v in self.canvas.tool_styles.items()}
        self._settings.setValue("tool_styles", json.dumps(styles))
        try:
            beautify.switch().changed.disconnect(self._show_beautify)  # a closed editor stops following it
        except (RuntimeError, TypeError):
            pass  # already disconnected (closed twice)
        super().closeEvent(e)
