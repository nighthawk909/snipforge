"""Text annotations: free text, and callouts (a shape with text inside and an optional pointer)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QTextCharFormat, QTextCursor, QTextOption
from PySide6.QtWidgets import QGraphicsTextItem, QStyle, QStyleOptionGraphicsItem

from ..fonts import ui_font
from .items import HIT_SLOP, Annotation, BoxItem, fill_ink, make_pen, readable_on, stroke_band
from .style import Style, fill_qcolor

ALIGN = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight}


class EditableText(QGraphicsTextItem):
    """Editable text that reports to its owning annotation when an edit finishes.

    The owner exposes `on_edit_finished(owner, before_state | None)` (set by the
    canvas) and `is_new`. `before_state` is None for an item that isn't in the
    undo history yet.
    """

    def __init__(self, parent=None) -> None:
        QGraphicsTextItem.__init__(self, parent)
        self._edit_before: dict | None = None

    def owner(self):
        return self

    def is_editing(self) -> bool:
        return bool(self.textInteractionFlags() & Qt.TextEditorInteraction)

    def start_editing(self) -> None:
        if self.is_editing():
            return  # keep the original undo baseline when re-entered (e.g. double-click mid-edit)
        owner = self.owner()
        if not getattr(owner, "is_new", False):
            self._edit_before = owner.get_state()
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setAcceptedMouseButtons(Qt.LeftButton | Qt.RightButton)
        self.setFocus(Qt.MouseFocusReason)
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.setTextCursor(cursor)

    def finish_editing(self) -> None:
        if not self.is_editing():
            return
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)
        self._after_editing()
        before, self._edit_before = self._edit_before, None
        owner = self.owner()
        callback = getattr(owner, "on_edit_finished", None)
        if callback:
            callback(owner, before)

    def _after_editing(self) -> None:
        pass

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if event.reason() != Qt.PopupFocusReason:  # right-click menu: keep editing so Paste works
            self.finish_editing()

    def paint(self, p: QPainter, option, widget=None) -> None:
        opt = QStyleOptionGraphicsItem(option)  # our overlay draws selection, not Qt
        opt.state = opt.state & ~QStyle.State_Selected & ~QStyle.State_HasFocus
        super().paint(p, opt, widget)


def _font(size: int, weight: QFont.Weight, style: Style | None = None) -> QFont:
    """The text's font: the usual one or the family in `style`, at `size`, with its italic and underline."""
    font = ui_font(int(size))
    if style is not None and style.font_family:
        font.setFamily(style.font_family)
    font.setWeight(weight)
    if style is not None:
        font.setItalic(style.italic)
        font.setUnderline(style.underline)
    return font


def apply_alignment(text: QGraphicsTextItem, align: str, wrap: QTextOption.WrapMode | None = None) -> None:
    """Align the text's lines; only when it changes, since a new option lays the document out again."""
    current = text.document().defaultTextOption()
    wanted = ALIGN.get(align, Qt.AlignLeft)
    if current.alignment() == wanted and (wrap is None or current.wrapMode() == wrap):
        return
    option = QTextOption(wanted)
    option.setWrapMode(wrap if wrap is not None else current.wrapMode())
    text.document().setDefaultTextOption(option)


# ---------------------------------------------------------------------------
# Free text
# ---------------------------------------------------------------------------
class TextItem(Annotation, EditableText):
    """Text, optionally on a filled background."""

    kind = "text"

    def __init__(self, style: Style, text: str = "") -> None:
        EditableText.__init__(self)
        self.on_edit_finished = None
        self.is_new = False
        self._outlining = False
        self.document().setDocumentMargin(8)
        self.setPlainText(text)
        self.init_annotation(style)
        self._apply_text_style()
        self.document().contentsChanged.connect(self._apply_outline)  # new letters get the outline too

    def set_style(self, style: Style) -> None:
        super().set_style(style)
        self._apply_text_style()

    def _apply_text_style(self) -> None:
        self.setFont(_font(self.style.font_size, QFont.Bold if self.style.bold else QFont.Normal, self.style))
        self.setDefaultTextColor(fill_ink(self.kind, self.style) if self.style.fill else QColor(self.style.color))
        apply_alignment(self, self.style.align)
        self._apply_outline()

    def _apply_outline(self) -> None:
        """Outline: a thin edge in the opposite shade around every letter, so text reads on anything."""
        if self._outlining:
            return
        self._outlining = True
        try:
            fmt = QTextCharFormat()
            if self.style.outline:
                edge = readable_on(QColor(self.style.color))
                fmt.setTextOutline(QPen(edge, max(0.8, self.style.font_size / 14), Qt.SolidLine, Qt.RoundCap,
                                        Qt.RoundJoin))
            else:
                fmt.setTextOutline(QPen(Qt.NoPen))
            cursor = QTextCursor(self.document())
            cursor.select(QTextCursor.SelectionType.Document)
            cursor.mergeCharFormat(fmt)
        finally:
            self._outlining = False

    def _geometry(self) -> dict:
        return {"text": self.toPlainText()}

    def _set_geometry(self, state: dict) -> None:
        self.setPlainText(state["text"])

    def is_degenerate(self) -> bool:
        return not self.toPlainText().strip()

    def start_text_editing(self) -> None:
        self.start_editing()

    def mouseDoubleClickEvent(self, event) -> None:
        if not self.is_editing():
            self.start_editing()
        super().mouseDoubleClickEvent(event)

    def paint(self, p: QPainter, option, widget=None) -> None:
        if self.style.fill:
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(fill_qcolor(self.kind, self.style))
            p.drawRoundedRect(self.boundingRect(), 7, 7)
        super().paint(p, option, widget)


# ---------------------------------------------------------------------------
# Callout
# ---------------------------------------------------------------------------
class _CalloutText(EditableText):
    def __init__(self, callout: CalloutItem) -> None:
        super().__init__(callout)
        self.setAcceptedMouseButtons(Qt.NoButton)  # clicks go to the callout unless editing
        apply_alignment(self, "center", QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(2)

    def owner(self):
        return self.parentItem()

    def _after_editing(self) -> None:
        self.setAcceptedMouseButtons(Qt.NoButton)


class CalloutItem(BoxItem):
    """A box, rounded box, ellipse or pill with centred text and a draggable pointer."""

    kind = "callout"
    PAD = 12.0
    MIN_SIZE = 24.0
    min_size = (MIN_SIZE, MIN_SIZE)

    def __init__(self, style: Style, text: str = "") -> None:
        self._tail = QPointF(0, 0)
        self._grab = QPointF(0, 0)  # from where a pointer drag was grabbed to the tip
        self._laying_out = False
        self.text: _CalloutText | None = None
        self.on_edit_finished = None
        self.is_new = False
        super().__init__(style)
        self.text = _CalloutText(self)
        self.text.setPlainText(text)
        self.text.document().contentsChanged.connect(self._relayout)
        self._apply_text_style()

    # -- text --------------------------------------------------------------
    def start_text_editing(self) -> None:
        self.text.start_editing()

    def plain_text(self) -> str:
        return self.text.toPlainText() if self.text else ""

    def set_style(self, style: Style) -> None:
        super().set_style(style)
        self._apply_text_style()

    def _apply_text_style(self) -> None:
        if self.text is None:
            return
        self.text.setFont(_font(self.style.font_size, QFont.DemiBold, self.style))
        self.text.setDefaultTextColor(self._text_color())
        # Callouts center their words unless you choose a side (their "left" is the usual centering)
        apply_alignment(self.text, "center" if self.style.align == "left" else self.style.align,
                        QTextOption.WrapAtWordBoundaryOrAnywhere)
        self._relayout()

    def _text_color(self) -> QColor:
        return fill_ink(self.kind, self.style) if self.style.fill else QColor("#1c1c1e")  # else on the white box

    def _insets(self) -> tuple[float, float]:
        r = self._rect
        if self.style.shape == "ellipse":
            return max(self.PAD, r.width() * 0.15), max(self.PAD * 0.6, r.height() * 0.15)
        if self.style.shape == "pill":
            return max(self.PAD, r.height() * 0.35), self.PAD * 0.6
        return self.PAD, self.PAD * 0.75

    def _relayout(self) -> None:
        if self.text is None or self._laying_out:
            return
        self._laying_out = True
        try:
            ix, iy = self._insets()
            self.text.setTextWidth(max(20.0, self._rect.width() - 2 * ix))
            text_h = self.text.boundingRect().height()
            needed = text_h + 2 * iy
            if needed > self._rect.height():  # grow to fit what you type
                self.prepareGeometryChange()
                self._rect.setHeight(needed)
            self.text.setPos(ix, (self._rect.height() - text_h) / 2)
        finally:
            self._laying_out = False

    # -- geometry ------------------------------------------------------------
    def _geometry_changed(self) -> None:
        self._relayout()

    def reset_tail(self) -> None:
        """Default pointer: down and to the left, outside the box."""
        self.prepareGeometryChange()
        self._tail = QPointF(self._rect.width() * 0.22, self._rect.height() + max(28.0, self._rect.height() * 0.6))
        self.update()

    def set_scene_rect(self, rect: QRectF) -> None:
        tail_scene = self.mapToScene(self._tail)  # resizing keeps the pointer where it points
        super().set_scene_rect(rect)
        self._tail = self.mapFromScene(tail_scene)

    def _geometry(self) -> dict:
        return {"size": [self._rect.width(), self._rect.height()],
                "tail": [self._tail.x(), self._tail.y()], "text": self.plain_text()}

    def _set_geometry(self, state: dict) -> None:
        self._rect = QRectF(0, 0, *state["size"])
        self._tail = QPointF(*state.get("tail", [0, self._rect.height() + 30]))
        if self.text is not None:
            self.text.setPlainText(state.get("text", ""))
        self._relayout()

    def is_degenerate(self) -> bool:
        return self._rect.width() < self.MIN_SIZE or self._rect.height() < self.MIN_SIZE

    def handle_points(self) -> dict[str, QPointF]:
        points = super().handle_points()
        if self.style.tail:
            points["tail"] = self.mapToScene(self._tail)
        return points

    def area_handle_at(self, scene_pos: QPointF) -> str | None:
        """Once selected, the pointer can be grabbed anywhere along it, not just by the dot at its tip."""
        pointer = self._pointer()
        local = self.mapFromScene(scene_pos)
        if pointer is None or self._box().contains(local):
            return None
        grab = pointer.united(stroke_band(pointer, HIT_SLOP))  # it narrows to a point: give it some width
        return "tail" if grab.contains(local) else None

    def begin_handle_drag(self) -> None:
        super().begin_handle_drag()
        self._grab = QPointF(0, 0)

    def press_at(self, scene_pos: QPointF) -> None:
        """Where a handle drag started: the tip moves by as much as the pointer, never jumps to it."""
        self._grab = self.mapToScene(self._tail) - scene_pos

    def drag_handle(self, name: str, scene_pos: QPointF, shift: bool) -> None:
        if name == "tail":
            self.prepareGeometryChange()
            self._tail = self.mapFromScene(scene_pos + self._grab)
            self.update()
        else:
            super().drag_handle(name, scene_pos, shift)

    # -- painting ------------------------------------------------------------
    def _box(self) -> QPainterPath:
        r, shape = self._rect, self.style.shape
        path = QPainterPath()
        if shape == "ellipse":
            path.addEllipse(r)
        elif shape == "pill":
            radius = min(r.width(), r.height()) / 2
            path.addRoundedRect(r, radius, radius)
        elif shape == "rect":
            path.addRect(r)
        else:
            path.addRoundedRect(r, 10, 10)
        return path

    def _pointer(self) -> QPainterPath | None:
        """The pointer's wedge, from the middle of the box to the tip; None when it's off or inside the box."""
        r, tip = self._rect, self._tail
        if not self.style.tail or self._box().contains(tip):
            return None
        center = r.center()
        dx, dy = tip.x() - center.x(), tip.y() - center.y()
        length = math.hypot(dx, dy)
        if length < 1:
            return None
        half = max(6.0, min(r.width(), r.height()) * 0.16)
        nx, ny = -dy / length * half, dx / length * half
        wedge = QPainterPath(QPointF(center.x() + nx, center.y() + ny))
        wedge.lineTo(tip)
        wedge.lineTo(QPointF(center.x() - nx, center.y() - ny))
        wedge.closeSubpath()
        return wedge

    def _body(self) -> QPainterPath:
        box, pointer = self._box(), self._pointer()
        return box if pointer is None else box.united(pointer)

    def boundingRect(self) -> QRectF:
        m = self.style.width + HIT_SLOP / 2 + 3  # room for the pointer's grab area too
        area = QRectF(self._rect)
        if self.style.tail:  # QRectF.united() ignores zero-size rects, so give the tip some area
            area = area.united(QRectF(self._tail.x() - 1, self._tail.y() - 1, 2, 2))
        return area.adjusted(-m, -m, m, m)

    def shape(self) -> QPainterPath:
        return self._body()

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        color = QColor(self.style.color)
        p.setRenderHint(QPainter.Antialiasing)
        if self.style.fill:
            border = color if self.style.fill_color else color.darker(125)  # usual look: a darker edge
            p.setPen(make_pen(border, max(1.0, self.style.width)))
            p.setBrush(fill_qcolor(self.kind, self.style))
        else:
            p.setPen(make_pen(color, max(1.0, self.style.width)))
            p.setBrush(QColor("#ffffff"))
        p.drawPath(self._body())

    def mouseDoubleClickEvent(self, event) -> None:
        self.start_text_editing()
