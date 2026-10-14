"""Editor window shown right after a snip is taken (or reopened from the library)."""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QIcon, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import ocr, redact
from ..dragout import DragOutButton
from ..icons import icon
from ..library import Entry, Library
from ..settings import app_settings
from .beautify import apply_beautify
from .canvas import Canvas
from .props import PropertyBar
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
RECENT_COUNT = 12

STYLE = """
QMainWindow, #Root { background: #1f1f1f; }
#PropertyBar { background: #262626; border-bottom: 1px solid #333; }
#PropTitle { color: #f0f0f0; font-weight: 600; padding-right: 6px; }
#PropHint { color: #8a8a8a; }
QLabel { color: #b5b5b5; }
QCheckBox { color: #d0d0d0; spacing: 6px; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #5a5a5a; border-radius: 3px; background: #2a2a2a; }
QCheckBox::indicator:checked { background: #2563eb; border-color: #2563eb; }
QScrollBar:horizontal, QScrollBar:vertical { background: #1f1f1f; border: none; width: 10px; height: 10px; }
QScrollBar::handle { background: #444; border-radius: 4px; min-height: 30px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QToolBar#ToolRail { background: #262626; border-right: 1px solid #333; padding: 6px 4px; spacing: 2px; }
QToolBar#ToolRail QToolButton { border-radius: 6px; padding: 5px; }
QToolBar#ToolRail QToolButton:hover { background: #333; }
QToolBar#ToolRail QToolButton:checked { background: #1d4ed8; }
#BottomBar { background: #262626; border-top: 1px solid #333; }
#BottomBar QPushButton { color: #e5e5e5; background: #2f2f2f; border: 1px solid #3d3d3d;
                         border-radius: 6px; padding: 5px 10px; }
#BottomBar QPushButton:hover { background: #383838; }
#BottomBar QPushButton#Primary { background: #2563eb; border-color: #2563eb; color: white; }
#BottomBar QPushButton#Primary:hover { background: #1d4ed8; }
#CustomColor { color: #ddd; background: #333; border: 1px solid #555; border-radius: 6px; }
QPushButton { color: #e5e5e5; }
#Thumb { background: #1a1a1a; border: 1px solid #3a3a3a; border-radius: 4px; }
#Thumb:hover { border-color: #3b82f6; }
#ZoomLabel { color: #8a8a8a; padding: 0 6px; }
QMenu { background: #2a2a2a; color: #e5e5e5; border: 1px solid #3d3d3d; padding: 4px; }
QMenu::item { padding: 6px 22px 6px 10px; border-radius: 4px; }
QMenu::item:selected { background: #1d4ed8; }
QMenu::separator { height: 1px; background: #3d3d3d; margin: 4px 6px; }
QToolButton#ShapeButton { border-radius: 5px; padding: 3px; }
QToolButton#ShapeButton:hover { background: #333; }
QToolButton#ShapeButton:checked { background: #1d4ed8; }
QToolButton#StampPicker { color: #e5e5e5; background: #2f2f2f; border: 1px solid #3d3d3d; border-radius: 6px; padding: 2px 8px; }
QToolButton#StampChoice { border-radius: 6px; padding: 3px; }
QToolButton#StampChoice:hover { background: #3a3a3a; }
QPushButton#CropPrimary { background: #2563eb; color: white; border: none; border-radius: 6px; padding: 4px 12px; }
QPushButton#CropPrimary:hover { background: #1d4ed8; }
QPushButton#CropButton { background: #2f2f2f; color: #e5e5e5; border: 1px solid #3d3d3d; border-radius: 6px; padding: 4px 10px; }
QPushButton#CropButton:hover { background: #383838; }
QSlider::groove:horizontal { height: 4px; background: #444; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -6px 0; border-radius: 7px; background: #e5e5e5; }
QSpinBox { background: #2f2f2f; color: #e5e5e5; border: 1px solid #3d3d3d; border-radius: 4px; padding: 2px; }
"""


def load_tool_styles(settings: QSettings) -> dict[str, Style]:
    raw = settings.value("tool_styles", "")
    try:
        data = json.loads(raw) if raw else {}
        return {k: Style.from_dict(v) for k, v in data.items()}
    except (TypeError, ValueError):
        return {}


class EditorWindow(QMainWindow):
    def __init__(self, image: QImage, library: Library, entry: Entry | None, document: dict | None = None,
                 on_new_snip=None, on_open_entry=None, on_pin=None, autosave: bool = True) -> None:
        super().__init__()
        self.setStyleSheet(STYLE)
        self._library, self._entry = library, entry
        self._autosave_enabled = autosave and entry is not None
        self._fitted = False
        self._on_new_snip, self._on_open_entry, self._on_pin = on_new_snip, on_open_entry, on_pin
        self._settings = app_settings()
        self._export_path: Path | None = None

        self.canvas = Canvas(image, load_tool_styles(self._settings))
        if document:
            self.canvas.load_document(document)
        self.props = PropertyBar()
        self._tool_actions: dict[str, QAction] = {}

        root = QWidget()
        root.setObjectName("Root")
        column = QVBoxLayout(root)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.props)
        middle = QHBoxLayout()
        middle.setSpacing(0)
        middle.addWidget(self._build_tool_rail())
        middle.addWidget(self.canvas, 1)
        column.addLayout(middle, 1)
        column.addWidget(self._build_bottom_bar())
        self.setCentralWidget(root)
        self._build_shortcuts()

        self._autosave = QTimer(self, singleShot=True, interval=AUTOSAVE_MS)
        self._autosave.timeout.connect(self._save_to_library)
        self.canvas.document_changed.connect(self._autosave.start)
        self.canvas.selection_changed.connect(self._refresh_props)
        self.canvas.tool_changed.connect(lambda t: self._tool_actions[t].setChecked(True))
        self.canvas.zoom_changed.connect(lambda z: self._zoom_label.setText(f"{round(z * 100)}%"))
        self.props.changed.connect(lambda changes: self.canvas.apply_style(**changes))
        self.props.crop_action.connect(self._crop_action)

        self._update_title()
        self._refresh_props()
        self._fit_to_screen(image)
        self._refresh_recent()

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
            act = QAction(icon(name), tip, self)
            act.setCheckable(True)
            act.triggered.connect(lambda _=False, n=name: self._choose_tool(n))
            group.addAction(act)
            rail.addAction(act)
            self._tool_actions[name] = act
        self._tool_actions["select"].setChecked(True)
        return rail

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("BottomBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(6)

        self._recent_row = QHBoxLayout()
        self._recent_row.setSpacing(6)
        row.addLayout(self._recent_row)
        row.addStretch(1)

        self._zoom_label = QLabel("100%")
        self._zoom_label.setObjectName("ZoomLabel")
        row.addWidget(self._zoom_label)
        self._beautify = QCheckBox("Beautify")
        self._beautify.setToolTip("Gradient backdrop, rounded corners and shadow on copy, save and drag")
        self._beautify.setChecked(self._settings.value("beautify", False, bool))
        self._beautify.toggled.connect(lambda on: self._settings.setValue("beautify", on))
        row.addWidget(self._beautify)

        def button(text, icon_name, slot, tip, primary=False):
            b = QPushButton(icon(icon_name, "#ffffff" if primary else "#d4d4d4"), text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            if primary:
                b.setObjectName("Primary")
            row.addWidget(b)
            return b

        drag = DragOutButton(self.final_image)
        drag.setIcon(icon("drag"))
        row.addWidget(drag)
        button("Grab text", "ocr", self.grab_text, "Copy the text in this snip (Ctrl+Shift+T)")
        button("Redact", "redact", self.auto_redact, "Blur emails, phone and card numbers automatically")
        button("Pin", "pin", self.pin, "Float this snip on top of other windows (Ctrl+P)")
        share = button("Share", "share", lambda: None, "Email, Teams, OneDrive link and more")
        share.setMenu(self._build_share_menu(share))
        button("Save", "save", self.save, "Save PNG to Pictures\\SnipForge (Ctrl+S)")
        button("Copy", "copy", self.copy_to_clipboard, "Copy to clipboard (Ctrl+C)", primary=True)
        for w in bar.findChildren(QWidget):  # keep keyboard focus on the canvas
            w.setFocusPolicy(Qt.NoFocus)
        return bar

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
        add("Ctrl+P", self.pin)
        add("Ctrl+Shift+T", self.grab_text)
        add("Ctrl+W", self.close)
        if self._on_new_snip:
            add("Ctrl+N", self._on_new_snip)

    def _fit_to_screen(self, image: QImage) -> None:
        avail = QGuiApplication.primaryScreen().availableGeometry()
        w = min(max(image.width() + 120, 980), int(avail.width() * 0.92))
        h = min(max(image.height() + 160, 560), int(avail.height() * 0.92))
        self.resize(w, h)
        self.move(avail.center().x() - w // 2, avail.center().y() - h // 2)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._fitted:  # only on first show, so restoring from minimize keeps your zoom
            self._fitted = True
            QTimer.singleShot(0, self.canvas.fit_image)
        self.canvas.setFocus()

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._refresh_recent()

    # ------------------------------------------------------------------ state sync
    def _choose_tool(self, name: str) -> None:
        self.canvas.set_tool(name)
        self.canvas.setFocus()

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
        self.props.load(kind, style, bool(self.canvas.selected_annotations()))

    def _refresh_recent(self) -> None:
        while self._recent_row.count():
            widget = self._recent_row.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        current = self._entry.id if self._entry else None
        entries = [e for e in self._library.entries(RECENT_COUNT + 1) if e.id != current][:RECENT_COUNT]
        if not entries:
            return
        label = QLabel("Recent")
        self._recent_row.addWidget(label)
        for entry in entries:
            thumb = QToolButton()
            thumb.setObjectName("Thumb")
            pix = QPixmap(str(entry.preview_path))
            if not pix.isNull():
                thumb.setIcon(QIcon(pix.scaled(96, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            thumb.setIconSize(QSize(48, 28))
            thumb.setToolTip(entry.created.strftime("%b %d, %I:%M %p") + " — click to open")
            thumb.clicked.connect(lambda _=False, i=entry.id: self._on_open_entry and self._on_open_entry(i))
            thumb.setFocusPolicy(Qt.NoFocus)
            self._recent_row.addWidget(thumb)

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
        return apply_beautify(image) if self._beautify.isChecked() else image

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
            self.statusBar().showMessage(f"Couldn't autosave: {exc}", 5000)

    def closeEvent(self, e) -> None:
        self.canvas.commit_text()
        self.canvas.apply_crop()
        self._autosave.stop()
        self.canvas.closing = True
        self._save_to_library()
        styles = {k: v.to_dict() for k, v in self.canvas.tool_styles.items()}
        self._settings.setValue("tool_styles", json.dumps(styles))
        super().closeEvent(e)
