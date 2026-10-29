"""The editor changes snip in place: a new snip, a Recent one or one from the Library shows in the
editor that's open, which keeps its position, size, tool and panels. Each snip has a canvas of its
own, and the last few you visited keep theirs (and so their undo history) while the editor is open;
older ones come back from the Library, markup and all."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QTimer

from . import colors
from .beautify import DEFAULT_STYLE
from .canvas import Canvas, UnsupportedDocument
from .chrome import RECENT_COUNT

KEEP_UNDO_FOR = 5  # snips whose canvas (and undo history) the editor keeps while it's open


@dataclass
class OpenSnip:
    """A snip the editor has a canvas for."""
    entry: object  # library.Entry
    canvas: Canvas
    autosave: bool
    export_path: Path | None = None
    fitted: bool = False


class SwitchMixin:
    """For EditorWindow. Uses its `canvas`, `_entry`, `_autosave_enabled`, `_export_path`, `_library`,
    `_canvases` (a QStackedWidget), `_autosave` (timer), `panel`, `tray`, `commands`, `_beautify`,
    `_on_open_entry`, `_on_left_snip` and the refresh methods."""

    def _start_switching(self) -> None:
        self._kept: OrderedDict[str, OpenSnip] = OrderedDict()
        self._wired: list = []
        self._current = OpenSnip(self._entry, self.canvas, self._autosave_enabled, fitted=True)  # showEvent fits it
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

    # ------------------------------------------------------------------ changing snip
    def show_snip(self, image, entry, document: dict | None, beautify_on: bool | None = None) -> None:
        """Show this snip here (a new one, or one from Recent or the Library). The snip before is
        saved to the Library first. The app opens a snip that can't autosave in a window of its own."""
        if entry is not None and entry.id in self._kept:
            self._activate(self._kept[entry.id])
            return
        canvas = Canvas(image, dict(self.canvas.tool_styles))
        autosave = entry is not None
        if document:
            try:
                canvas.load_document(document)
            except UnsupportedDocument:
                autosave = False
        on = self._beautify if beautify_on is None else beautify_on
        canvas.set_beautify(DEFAULT_STYLE if on else None)
        self._canvases.addWidget(canvas)
        snip = OpenSnip(entry, canvas, autosave)
        if entry is not None:
            self._kept[entry.id] = snip
        self._activate(snip)
        self._let_go_of_old_snips()

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
        snip.canvas.setFocus()

    def _leave_current(self) -> None:
        """Save the snip on show before another takes its place, and tell the app (Library search)."""
        canvas = self.canvas
        canvas.stop_picking()
        canvas.commit_text()
        canvas.apply_crop()
        self._autosave.stop()
        self._current.export_path = self._export_path
        saved = self._save_to_library()
        if saved and self._entry is not None and self._on_left_snip:
            self._on_left_snip(self._entry)

    def _let_go_of_old_snips(self) -> None:
        """Keep the canvases of the last few snips shown; older ones were saved as they were left."""
        while len(self._kept) > KEEP_UNDO_FOR:
            entry_id, old = next(iter(self._kept.items()))
            if old is self._current:
                break
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
        if self._autosave_enabled:
            self.tray.set_saved("Saved", "Changes save to your library automatically")
        else:
            self.tray.set_saved("Autosave off", "Use Save or Copy to keep what you change")
        self._refresh_recent()

    # ------------------------------------------------------------------ the canvas on show
    def _wire(self, canvas: Canvas) -> None:
        """Connect the canvas on show to the window; _unwire undoes exactly this."""
        pairs = [
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
