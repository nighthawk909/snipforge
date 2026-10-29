"""The editor changes snip in place: a new snip, a Recent one or one from the Library shows in the
editor that's open, which keeps its position, size, tool and panels. Each snip has a canvas of its
own, and the last few you visited keep theirs (and so their undo history) while the editor is open;
older ones come back from the Library, markup and all."""
from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from . import beautify, colors
from .beautify import DEFAULT_STYLE
from .canvas import Canvas, UnsupportedDocument
from .chrome import RECENT_COUNT

KEEP_UNDO_FOR = 5  # snips whose canvas (and undo history) the editor keeps while it's open

log = logging.getLogger(__name__)


@dataclass
class OpenSnip:
    """A snip the editor has a canvas for."""
    entry: object  # library.Entry
    canvas: Canvas
    autosave: bool
    export_path: Path | None = None
    fitted: bool = False
    dirty: bool = False  # changed since it was last saved to the Library
    beautify: bool = False  # Beautify on for this snip (a capture preset can choose it)


class SwitchMixin:
    """For EditorWindow. Uses its `canvas`, `_entry`, `_autosave_enabled`, `_export_path`, `_library`,
    `_canvases` (a QStackedWidget), `_autosave` (timer), `panel`, `tray`, `commands`, `_beautify`,
    `_on_open_entry`, `_on_left_snip` and the refresh methods."""

    def _start_switching(self) -> None:
        self._kept: OrderedDict[str, OpenSnip] = OrderedDict()
        self._wired: list = []
        self._current = OpenSnip(self._entry, self.canvas, self._autosave_enabled, fitted=True,  # showEvent fits it
                                 beautify=self._beautify)
        if self._entry is not None:
            self._kept[self._entry.id] = self._current
        self._wire(self.canvas)

    # ------------------------------------------------------------------ what's open
    def entry_id(self) -> str | None:
        return self._entry.id if self._entry is not None else None

    def kept_snips(self) -> list[str]:
        """The snips whose canvas is kept, least recently shown first."""
        return list(self._kept)

    def has_snip(self, entry_id: str) -> bool:
        return entry_id in self._kept

    def has_open_dialog(self) -> bool:
        """A dialog of this editor's is waiting on you (Save As, Resize, Edit text): a snip taken now
        mustn't take the place of the one it's working on."""
        widget = QApplication.activeModalWidget()
        while widget is not None:
            if widget is self:
                return True
            widget = widget.parentWidget()
        return False

    # ------------------------------------------------------------------ changing snip
    def show_snip(self, image, entry, document: dict | None, beautify_on: bool | None = None) -> bool:
        """Show this snip here (a new one, or one from Recent or the Library); the snip before is
        saved to the Library first. False if it can't autosave here (its markup is from a newer
        SnipForge): the app opens it in a window of its own, as it does any snip that can't autosave."""
        if entry is None:
            return False
        if entry.id in self._kept:
            self._activate(self._kept[entry.id])
            return True
        canvas = Canvas(image, dict(self.canvas.tool_styles))
        if document:
            try:
                canvas.load_document(document)
            except UnsupportedDocument:
                canvas.deleteLater()
                return False
        on = beautify.enabled() if beautify_on is None else beautify_on
        canvas.set_beautify(DEFAULT_STYLE if on else None)
        self._canvases.addWidget(canvas)
        snip = OpenSnip(entry, canvas, True, beautify=on)
        self._kept[entry.id] = snip
        self._activate(snip)
        self._let_go_of_old_snips()
        return True

    def switch_to(self, entry_id: str) -> bool:
        """Show a snip whose canvas is kept. False if it isn't."""
        snip = self._kept.get(entry_id)
        if snip is None:
            return False
        self._activate(snip)
        return True

    def step_snip(self, older: bool) -> None:
        """Ctrl+PgDn / Ctrl+PgUp: the next older or newer snip in the Library."""
        ids = [e.id for e in self._library.entries()]
        if self.entry_id() not in ids:
            return
        at = ids.index(self.entry_id()) + (1 if older else -1)
        if 0 <= at < len(ids) and self._on_open_entry:
            self._on_open_entry(ids[at])

    def _activate(self, snip: OpenSnip) -> None:
        if snip is self._current:
            return
        tool, styles = self.canvas.tool, dict(self.canvas.tool_styles)
        self._leave_current()
        self._unwire(self.canvas)
        self._current = snip
        self.canvas, self._entry = snip.canvas, snip.entry
        self._autosave_enabled, self._export_path = snip.autosave, snip.export_path
        self._beautify = snip.beautify
        self.tray.set_beautify(snip.beautify)
        if snip.entry is not None:
            self._kept.move_to_end(snip.entry.id)
        self._canvases.setCurrentWidget(snip.canvas)
        self._wire(snip.canvas)
        snip.canvas.tool_styles = styles  # the tools look as you left them, whichever snip this is
        snip.canvas.set_tool(tool)
        self._show_snip_state()
        if not snip.fitted:
            snip.fitted = True
            QTimer.singleShot(0, snip.canvas, snip.canvas.fit_image)  # once it has its size
        if snip.dirty:
            self._autosave.start()  # it couldn't be saved when you left it: try again
        snip.canvas.setFocus()

    def _leave_current(self) -> None:
        """Save the snip on show before another takes its place, and tell the app (Library search)."""
        canvas = self.canvas
        canvas.stop_picking()
        canvas.stop_choosing()
        canvas.commit_text()
        canvas.apply_crop()
        self._autosave.stop()
        self._current.export_path = self._export_path
        if not self._current.dirty:
            return  # only looked at: nothing to write, or to read again for search
        if self._save_to_library():
            self._told_app(self._current)
        elif self._autosave_enabled:
            self.statusBar().showMessage("Couldn't save the snip you left. It's kept here, and SnipForge tries "
                                         "again.", 8000)

    def _told_app(self, snip: OpenSnip) -> None:
        if snip.entry is not None and self._on_left_snip:
            self._on_left_snip(snip.entry)

    def _snip_changed(self) -> None:
        self._current.dirty = True

    def _save_kept(self, snip: OpenSnip) -> bool:
        """Save a snip that isn't on show (one whose save failed as you left it). True if it's saved."""
        if not snip.autosave or not snip.dirty:
            return not snip.dirty
        try:
            self._library.save(snip.entry, snip.canvas.to_document(),
                               snip.canvas.render_image(commit=False, use_draft=False))
        except OSError as exc:
            log.warning("Couldn't save snip %s: %s", snip.entry.id, exc)
            return False
        snip.dirty = False
        self._told_app(snip)
        return True

    def save_kept_snips(self) -> None:
        """As the editor closes: the snips not on show whose save failed as they were left."""
        for snip in list(self._kept.values()):
            if snip is not self._current:
                self._save_kept(snip)

    def _let_go_of_old_snips(self) -> None:
        """Keep the canvases of the last few snips shown; older ones were saved as they were left."""
        for entry_id, old in list(self._kept.items()):  # least recently shown first
            if len(self._kept) <= KEEP_UNDO_FOR:
                break
            if old is self._current or not self._save_kept(old):
                continue  # one whose changes aren't in the Library yet stays until they are
            del self._kept[entry_id]
            self._canvases.removeWidget(old.canvas)
            old.canvas.deleteLater()

    def _show_snip_state(self) -> None:
        self._update_title()
        self._refresh_props()
        self._refresh_size()
        self._refresh_history()
        self.commands.set_zoom(self.canvas.zoom())
        self.panel.set_source_stamp_available(self._entry is not None)
        self.commands.combine_button.setVisible(self._on_combine is not None and self._autosave_enabled)
        if not self._autosave_enabled:
            self.tray.set_saved("Autosave off", "Use Save or Copy to keep what you change")
        elif self._current.dirty:
            self.tray.set_saved("Not saved", "Its changes aren't in your library yet: SnipForge tries again")
        else:
            self.tray.set_saved("Saved", "Changes save to your library automatically")
        self._refresh_recent()

    # ------------------------------------------------------------------ the canvas on show
    def _wire(self, canvas: Canvas) -> None:
        """Connect the canvas on show to the window; _unwire undoes exactly this."""
        pairs = [
            (canvas.document_changed, self._snip_changed),
            (canvas.document_changed, self._autosave.start),
            (canvas.document_changed, self._refresh_size),
            (canvas.document_changed, self._mark_unsaved),
            (canvas.selection_changed, self._refresh_props),
            (canvas.tool_changed, self._follow_tool),
            (canvas.zoom_changed, self.commands.set_zoom),
            # Straight to the buttons (not a Python slot) so nothing runs while a stack is torn down.
            (canvas.undo_stack.canUndoChanged, self.commands.undo_button.setEnabled),
            (canvas.undo_stack.canRedoChanged, self.commands.redo_button.setEnabled),
            (canvas.notice, self._show_notice),
            (canvas.copy_requested, self.copy_to_clipboard),
            (canvas.picking_changed, self._show_picking),
            (canvas.choosing_changed, self._show_choosing),
            (canvas.color_picked, self._show_picked),
        ]
        for signal, slot in pairs:
            signal.connect(slot)
        self._wired = pairs
        self.panel.layers_list().attach(canvas)

    def _unwire(self, canvas: Canvas) -> None:
        for signal, slot in self._wired:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass  # already gone
        self._wired = []

    def _follow_tool(self, tool: str) -> None:
        self._tool_actions[tool].setChecked(True)

    def _show_notice(self, text: str) -> None:
        self.statusBar().showMessage(text, 6000)

    def _show_picked(self, color: str) -> None:
        self.statusBar().showMessage(f"Picked {color.upper()}", 3000)
        colors.remember(color)

    def recent_entries(self) -> list:
        """The newest snips, the one on show among them where it's new enough: the strip's order
        never changes as you move between them."""
        return self._library.entries(RECENT_COUNT)
