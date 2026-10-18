"""Report a problem: shows everything that would go in the report, then saves it where you choose."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from . import diagnostics, project

log = logging.getLogger(__name__)
BUG_FORM = f"{project.REPO_URL}/issues/new?template=bug_report.yml"
_open: list[ReportDialog] = []  # the one on screen, kept alive while it's open


class ReportDialog(QDialog):
    def __init__(self, report: diagnostics.Report, parent=None) -> None:
        super().__init__(parent)
        self.report = report
        self.saved_to: Path | None = None
        self.setWindowTitle("Report a problem")
        self.setObjectName("ReportDialog")
        self.resize(860, 600)
        col = QVBoxLayout(self)
        col.setContentsMargins(20, 18, 20, 16)
        col.setSpacing(10)
        title = QLabel("Report a problem")
        title.setProperty("sfRole", "title")
        intro = QLabel("SnipForge has put together these details to help fix the problem. Nothing has been sent "
                       "anywhere. Look through them, save them, and attach the file to your bug report.")
        intro.setWordWrap(True)
        intro.setProperty("sfRole", "secondary")
        never = QLabel(f"Never included: {diagnostics.NEVER_INCLUDED}.")
        never.setWordWrap(True)
        never.setProperty("sfRole", "muted")
        col.addWidget(title)
        col.addWidget(intro)
        col.addWidget(never)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.contents = QListWidget()
        self.contents.setAccessibleName("What's in the report")
        self.contents.setFixedWidth(250)
        for f in report.files:
            item = QListWidgetItem(f"{f.title}\n{f.name} · {_size(f.text)}")
            item.setData(Qt.UserRole, f.name)
            item.setToolTip(f.name)
            self.contents.addItem(item)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        mono = QFont()
        mono.setFamilies(["Cascadia Mono", "Consolas", "Menlo", "Monaco"])  # Windows, then the Mac
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(9)
        self.preview.setFont(mono)
        self.preview.setAccessibleName("The selected part of the report")
        self.contents.currentRowChanged.connect(self._show)
        body.addWidget(self.contents)
        body.addWidget(self.preview, 1)
        col.addLayout(body, 1)

        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.hide()
        col.addWidget(self.status)
        buttons = QHBoxLayout()
        self.show_folder = QPushButton("Show in folder")
        self.show_folder.clicked.connect(self._show_folder)
        self.show_folder.hide()
        self.bug_form = QPushButton("Open the bug report form")
        self.bug_form.setToolTip("Opens GitHub in your browser, where you can describe the problem and attach the file")
        self.bug_form.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(BUG_FORM)))
        self.bug_form.setProperty("variant", "ghost")
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        self.save_button = QPushButton("Save…")
        self.save_button.setProperty("variant", "primary")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.choose_and_save)
        buttons.addWidget(self.bug_form)
        buttons.addWidget(self.show_folder)
        buttons.addStretch(1)
        buttons.addWidget(close)
        buttons.addWidget(self.save_button)
        col.addLayout(buttons)
        self.contents.setCurrentRow(0)
        self.contents.setFocus()

    def _show(self, row: int) -> None:
        self.preview.setPlainText(self.report.files[row].text if 0 <= row < len(self.report.files) else "")

    def choose_and_save(self) -> None:
        folder = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation) or str(Path.home())
        start = str(Path(folder) / self.report.default_name)
        path, _filter = QFileDialog.getSaveFileName(self, "Save the report", start, "Zip files (*.zip)")
        if path:
            self.save_to(Path(path))

    def save_to(self, path: Path) -> bool:
        if path.suffix.lower() != ".zip":
            path = path.with_name(path.name + ".zip")
            if path.exists() and QMessageBox.question(
                    self, "Report a problem", f"{path.name} is already there. Replace it?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return False  # the save dialog asked about the name typed, not this one
        try:
            diagnostics.save(self.report, path)
        except OSError as exc:
            log.warning("Couldn't save the problem report: %s", exc)
            self._say(f"Couldn't save the report there: {exc.strerror or exc}. Try another folder.", error=True)
            return False
        self.saved_to = path
        self._say(f"Saved to {path}. Attach this file to your bug report.")
        self.show_folder.show()
        return True

    def _say(self, text: str, error: bool = False) -> None:
        self.status.setProperty("sfRole", "error" if error else "secondary")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(text)
        self.status.show()

    def _show_folder(self) -> None:
        if self.saved_to is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.saved_to.parent)))


def _size(text: str) -> str:
    size = len(text.encode("utf-8"))
    return f"{size / 1024:.0f} KB" if size >= 1024 else f"{size} bytes"


def open_report(parent=None, library_root: Path | None = None) -> ReportDialog | None:
    """Put the report together and show it (the one already open comes to the front instead)."""
    for dialog in _open:
        dialog.showNormal()
        dialog.raise_()
        dialog.activateWindow()
        return dialog
    try:
        report = diagnostics.build(library_root=library_root)
    except Exception as exc:  # never a menu item that silently does nothing
        log.exception("Couldn't put the problem report together")
        QMessageBox.warning(parent, "Report a problem", f"SnipForge couldn't put the report together: {exc}")
        return None
    dialog = ReportDialog(report, parent)
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    _open.append(dialog)
    dialog.finished.connect(lambda _code, d=dialog: _open.remove(d) if d in _open else None)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
