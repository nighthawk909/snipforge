"""Copy, cut and paste markup (within a snip or between snips), pasting text as a text box, and the
canvas's right-click menu.

Copy puts the snip on the clipboard as a picture, as it always has, and the selected markup beside it
in SnipForge's own format; Paste in any SnipForge editor brings the markup back. Other apps only see
the picture, so Ctrl+C after drawing still pastes the whole snip into a chat or an email."""
from __future__ import annotations

import json
import logging

from PySide6.QtCore import QByteArray, QMimeData, QPointF, QRectF
from PySide6.QtGui import QFontMetricsF, QGuiApplication, QImage, QKeySequence
from PySide6.QtWidgets import QMenu

from . import paste_checks
from .commands import AddItemsCommand, RemoveItemsCommand
from .factory import create_item
from .magnify import MagnifyItem
from .text_items import TextItem

log = logging.getLogger(__name__)

MARKUP_MIME = "application/x-snipforge-markup"
PASTE_OFFSET = 16.0  # a paste onto its own copy lands this far down and right, as Duplicate does
MAX_PASTE_BYTES = 8_000_000  # the clipboard is outside data: refuse what no copy of ours makes
MAX_PASTE_ITEMS = 1000
MAX_PASTE_TEXT = paste_checks.MAX_TEXT
PASTE_TEXT_WIDTH = 0.8  # pasted text wraps at this much of the snip's width
UNDERLAY_Z = -100  # highlighter strokes and spotlights keep their layer under other markup


def wrap_text(text: str, metrics: QFontMetricsF, width: float) -> str:
    """`text` with line breaks so no line is wider than `width` (a single long word keeps its line)."""
    lines = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}" if line else word
            if not line or metrics.horizontalAdvance(candidate) <= width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return "\n".join(lines)


def _no_constants(name: str):
    raise ValueError(f"{name} isn't a number a snip uses")


def parse_markup(data: bytes) -> list[dict]:
    """The item states in clipboard markup; [] if it isn't markup we wrote."""
    if len(data) > MAX_PASTE_BYTES:
        return []
    try:
        doc = json.loads(data.decode("utf-8"), parse_constant=_no_constants)  # no NaN or Infinity
    except (UnicodeDecodeError, ValueError, RecursionError):  # ValueError: bad JSON, endless digits...
        return []
    items = doc.get("items") if isinstance(doc, dict) and doc.get("version") == 1 else None
    if not isinstance(items, list):
        return []
    return [s for s in items[:MAX_PASTE_ITEMS] if isinstance(s, dict) and isinstance(s.get("kind"), str)]


class ClipboardMixin:
    """For the Canvas: uses its scene, selection, undo stack, `notice` and `copy_requested`."""

    # ------------------------------------------------------------------ copy and cut
    def markup_data(self) -> bytes | None:
        """The selected markup in SnipForge's clipboard format, or None if nothing is selected."""
        items = self.bottom_up(self.selected_annotations())
        states = [i.get_state() for i in items if not i.is_degenerate()]
        return json.dumps({"version": 1, "items": states}).encode("utf-8") if states else None

    def copy_mime(self, image: QImage) -> QMimeData:
        """What Copy puts on the clipboard: the snip as a picture, plus the selected markup."""
        mime = QMimeData()
        mime.setImageData(image)
        data = self.markup_data()
        if data is not None:
            mime.setData(MARKUP_MIME, QByteArray(data))
        return mime

    def cut_selection(self) -> bool:
        """Copy the selected markup and remove it (one undo step). False if nothing was selected."""
        data = self.markup_data()
        items = self.selected_annotations()
        if data is None or not items:
            return False
        mime = QMimeData()
        mime.setData(MARKUP_MIME, QByteArray(data))
        QGuiApplication.clipboard().setMimeData(mime)
        self.undo_stack.push(RemoveItemsCommand(self._scene, items, "Cut"))
        return True

    # ------------------------------------------------------------------ paste
    def can_paste(self) -> bool:
        mime = QGuiApplication.clipboard().mimeData()  # hasText, not text(): a big copy converts slowly
        return mime is not None and (mime.hasFormat(MARKUP_MIME) or mime.hasText())

    def paste(self) -> bool:
        """Paste markup copied in any SnipForge editor, or text from anywhere as a text box."""
        mime = QGuiApplication.clipboard().mimeData()
        if mime is not None and mime.hasFormat(MARKUP_MIME):
            raw = mime.data(MARKUP_MIME)
            if raw.size() > MAX_PASTE_BYTES:
                self.notice.emit("Couldn't paste that: it's far more markup than a snip holds.")
                return False
            return self.paste_markup(bytes(raw))
        text = mime.text() if mime is not None and mime.hasText() else ""
        if text.strip():
            return self.paste_text(text)
        if mime is not None and mime.hasImage():
            self.notice.emit("Pasting a picture into a snip isn't possible yet. Copy markup from a snip, or text.")
        else:
            self.notice.emit("Nothing to paste. Copy markup from a snip, or some text.")
        return False

    def paste_markup(self, data: bytes) -> bool:
        states = parse_markup(data)
        why = paste_checks.problem(states, self._image_rect.size())
        if why:
            self.notice.emit(f"Couldn't paste that markup: {why}.")
            return False
        items = []
        for state in states:
            try:
                items.append(self._adopt(create_item(paste_checks.tidy(state), self.base_image, self._image_rect)))
            except Exception as exc:  # outside data: whatever a damaged state makes the item raise
                log.warning("Skipped markup this version can't paste (%.80r): %.200s", state.get("kind"), exc)
        if not items:
            self.notice.emit("Couldn't paste that: it isn't markup this version of SnipForge can read.")
            return False
        self.commit_text()
        self._place_pasted(items)
        self.undo_stack.push(AddItemsCommand(self._scene, items, "Paste"))
        self._select(items)
        return True

    def paste_text(self, text: str) -> bool:
        """Text from anywhere becomes a text box in the middle of the part of the snip you can see."""
        self.commit_text()
        text = text.strip()
        if len(text) > MAX_PASTE_TEXT:
            self.notice.emit(f"Pasted the first {MAX_PASTE_TEXT:,} characters.")
            text = text[:MAX_PASTE_TEXT]
        item = self._adopt(TextItem(self.tool_styles["text"]))
        room = self._crop_rect.width() * PASTE_TEXT_WIDTH - 2 * item.document().documentMargin()
        item.setPlainText(wrap_text(text, QFontMetricsF(item.font()), max(40.0, room)))  # a text box doesn't wrap
        box = item.boundingRect()
        middle = self._visible_middle()
        item.setPos(middle.x() - box.width() / 2, middle.y() - box.height() / 2)
        item.setZValue(self._next_z())
        self.undo_stack.push(AddItemsCommand(self._scene, [item], "Paste"))
        self._select([item])
        return True

    def _visible_middle(self) -> QPointF:
        visible = self.mapToScene(self.viewport().rect()).boundingRect().intersected(self._crop_rect)
        return (visible if not visible.isEmpty() else self._crop_rect).center()

    def _place_pasted(self, items: list) -> None:
        """Layer the paste on top, step it off a copy it would hide, and bring it into view."""
        top = self._next_z()
        for n, item in enumerate(items):
            if item.zValue() > UNDERLAY_Z:
                item.setZValue(top + n)
        taken = {(i.kind, round(i.pos().x()), round(i.pos().y())) for i in self.annotations()}

        def lands_on_a_copy(dx: float) -> bool:
            return any((i.kind, round(i.pos().x() + dx), round(i.pos().y() + dx)) in taken for i in items)

        step = 0.0
        while lands_on_a_copy(step) and step < PASTE_OFFSET * 50:
            step += PASTE_OFFSET
        shift = QPointF(step, step)
        bounds = QRectF()
        for item in items:
            bounds = bounds.united(item.sceneBoundingRect().translated(shift))
        crop = self._crop_rect
        into_view = QPointF(0, 0)
        if not bounds.intersects(crop):  # copied from somewhere this snip doesn't reach
            into_view = crop.topLeft() + QPointF(PASTE_OFFSET, PASTE_OFFSET) - bounds.topLeft()
        for item in items:
            item.moveBy(shift.x() + into_view.x(), shift.y() + into_view.y())
            if isinstance(item, MagnifyItem) and not into_view.isNull():
                item.set_source(item.source().translated(into_view))  # what it shows comes along

    # ------------------------------------------------------------------ right-click menu
    def contextMenuEvent(self, e) -> None:  # noqa: N802 - Qt API
        if self._skip_menu:
            self._skip_menu = False
            return
        menu = self.context_menu(e.pos())
        if menu is None:
            super().contextMenuEvent(e)  # e.g. the text you're typing in has its own menu
            return
        menu.exec(e.globalPos())
        menu.deleteLater()

    def context_menu(self, view_pos) -> QMenu | None:
        """The menu a right-click at `view_pos` shows (selecting the markup under it first), or None
        where the click belongs to something else: the text you're typing (its own menu), a gesture."""
        if self.picking() or self._drag is not None:
            return None
        editing = self.editing_text_item()
        hit = self._annotation_at(view_pos)
        if editing is not None and hit is editing.owner():
            return None
        self.commit_text()
        if hit is not None and not hit.isSelected():
            self._select([hit])
        selected = bool(self.selected_annotations())
        menu = QMenu(self)
        entries = (
            ("Cut", "Ctrl+X", self.cut_selection, selected),
            ("Copy" if selected else "Copy snip", "Ctrl+C", self.copy_requested.emit, True),
            ("Paste", "Ctrl+V", self.paste, self.can_paste()),
            ("Duplicate", "Ctrl+D", self.duplicate_selection, selected),
            ("Delete", "Del", self.delete_selection, selected),
            None,
            ("Bring to front", "Ctrl+]", lambda: self.reorder_selection(True), selected),
            ("Send to back", "Ctrl+[", lambda: self.reorder_selection(False), selected),
            None,
            ("Select all", "Ctrl+A", self.select_all, bool(self.annotations())),
        )
        for entry in entries:
            if entry is None:
                menu.addSeparator()
                continue
            label, shortcut, slot, enabled = entry
            action = menu.addAction(label, slot)
            action.setShortcut(QKeySequence(shortcut))  # shown beside it; the window owns the real shortcut
            action.setShortcutVisibleInContextMenu(True)
            action.setEnabled(enabled)
        return menu
