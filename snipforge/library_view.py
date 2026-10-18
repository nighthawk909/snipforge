"""The Library's grid: date headers and snip cards in one wrapping list, painted from the theme."""
from __future__ import annotations

import contextlib
import datetime as dt
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass, replace

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QPointF,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFont,
    QFontMetrics,
    QImage,
    QImageReader,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QAbstractItemView, QFrame, QListView, QStyle, QStyledItemDelegate

from . import theme
from .icons import icon
from .library import THUMB_MAX, EntryMeta
from .library_search import Item

log = logging.getLogger(__name__)

CARD_W, CARD_H, THUMB_H = 232, 204, 136
GAP = 8
HEADER_H = 40
RADIUS = 10
STAR_COLOR = "#F5B400"
THUMB_CACHE = 120  # decoded thumbnails kept while the Library is open (cleared when it's hidden)
VIDEO_PREVIEW_AT = 0.5  # seconds into a recording its card's picture is taken from
RECALL_ON_OPEN, RECALL_ON_DATA_ACCESS = 0x40000, 0x400000  # FILE_ATTRIBUTE_*: a cloud file not on this PC
ROW_ROLE = Qt.UserRole + 1


@dataclass(frozen=True)
class Row:
    label: str = ""
    item: Item | None = None

    @property
    def is_header(self) -> bool:
        return self.item is None


def when_text(created: dt.datetime) -> str:
    hour = created.strftime("%I").lstrip("0")
    return f"{created:%b} {created.day}, {hour}:{created:%M %p}"


class LibraryModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[Row] = []

    def set_groups(self, groups: list[tuple[str, list[Item]]]) -> None:
        self.beginResetModel()
        self._rows = []
        for label, items in groups:
            self._rows.append(Row(label=label))
            self._rows.extend(Row(item=i) for i in items)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: B008, N802 - Qt API
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if role == ROW_ROLE:
            return row
        if role in (Qt.DisplayRole, Qt.AccessibleTextRole):
            if row.is_header:
                return row.label
            item = row.item
            meta = item.meta
            parts = [meta.title or ("Recording" if item.entry.is_recording else "Snip")]
            if item.entry.is_recording:
                parts.append(" ".join(p for p in ("video", duration_label(item)) if p))
            parts += [meta.app, when_text(item.created)]
            if meta.favorite:
                parts.append("favorite")
            if meta.tags:
                parts.append("tags " + ", ".join(meta.tags))
            return ", ".join(p for p in parts if p)
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        if self._rows[index.row()].is_header:
            return Qt.ItemIsEnabled
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def card_count(self) -> int:
        return sum(1 for r in self._rows if not r.is_header)

    def row_for(self, entry_id: str) -> int:
        return next((n for n, r in enumerate(self._rows) if r.item and r.item.entry.id == entry_id), -1)

    def update_meta(self, entry_id: str, meta: EntryMeta) -> None:
        n = self.row_for(entry_id)
        if n < 0:
            return
        self._rows[n] = Row(item=replace(self._rows[n].item, meta=meta))
        self.dataChanged.emit(self.index(n), self.index(n))


def duration_label(item: Item) -> str:
    """A recording's length as its card shows it ("1:05"); empty for a snip or an unknown length."""
    from .video.recordings import duration_text

    return duration_text(item.duration) if item.duration is not None else ""


def _cloud_only(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & (RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS))


def _star(rect: QRectF) -> QPolygonF:
    import math

    cx, cy, outer = rect.center().x(), rect.center().y(), rect.width() / 2
    inner = outer * 0.45
    points = []
    for k in range(10):
        r = outer if k % 2 == 0 else inner
        a = -math.pi / 2 + k * math.pi / 5
        points.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    return QPolygonF(points)


class _Loaded(QObject):
    done = Signal(object, object)  # key, QImage (or None)


class _ThumbJob(QRunnable):
    """Decodes one thumbnail off the UI thread, from preview.png when there is one. For older snips
    without a preview it decodes the original once and saves a preview for next time; for a
    recording (`video`) it takes a frame from the video, once, likewise."""

    def __init__(self, key: tuple, path: str, target: QSize, make_preview: str | None, signals: _Loaded,
                 video: bool = False) -> None:
        super().__init__()
        self._key, self._path, self._target = key, path, target
        self._make_preview, self._signals, self._video = make_preview, signals, video

    def run(self) -> None:
        if self._video:
            self._run_video()
            return
        reader = QImageReader(self._path)
        size = reader.size()
        if size.isValid() and size.width() > 0 and size.height() > 0:
            scale = max(self._target.width() / size.width(), self._target.height() / size.height())
            if scale < 1:
                reader.setScaledSize(QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale))))
        image = reader.read()
        if image.isNull():
            self._signals.done.emit(self._key, None)
            return
        if self._make_preview:
            try:
                full = QImage(self._path)
                small = full.scaled(THUMB_MAX, THUMB_MAX, Qt.KeepAspectRatio, Qt.SmoothTransformation) \
                    if max(full.width(), full.height()) > THUMB_MAX else full
                tmp = self._make_preview + ".tmp"
                if small.save(tmp, "PNG"):
                    os.replace(tmp, self._make_preview)
            except OSError:
                pass  # just slower next time
        self._done(image)

    def _run_video(self) -> None:
        """The frame showing half a second in, saved as the recording's preview. The card's picture
        is handed over under the preview's key too, so it isn't decoded again from the PNG."""
        image = self._video_frame()
        if image is None:
            self._signals.done.emit(self._key, None)
            return
        if self._make_preview:
            tmp = self._make_preview + ".tmp"
            try:
                if image.save(tmp, "PNG"):
                    os.replace(tmp, self._make_preview)
                    self._done(image, (self._key[0], os.stat(self._make_preview).st_mtime_ns, *self._key[2:]))
            except OSError:
                pass  # just slower next time
        self._done(image)

    def _video_frame(self) -> QImage | None:
        """The last frame at or before VIDEO_PREVIEW_AT (the first, for a shorter video); None if
        there's none. A video that breaks off after a good frame still gives that frame."""
        from .video import frames

        image = None
        try:
            with contextlib.closing(frames.read(self._path, width=THUMB_MAX)) as reader:
                for seconds, frame in reader:
                    if image is not None and seconds > VIDEO_PREVIEW_AT:
                        break
                    image = frame
        except Exception as exc:  # FFmpegError: a damaged or unreadable video keeps its plain card
            log.info("No picture for the recording %s: %s", self._path, exc)
        return None if image is None or image.isNull() else image

    def _done(self, image: QImage, key: tuple | None = None) -> None:
        scaled = image.scaled(self._target, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = (scaled.width() - self._target.width()) // 2  # keep the top, where titles usually are
        self._signals.done.emit(key or self._key, scaled.copy(x, 0, self._target.width(), self._target.height()))


class CardDelegate(QStyledItemDelegate):
    favorite_clicked = Signal(str)
    copy_clicked = Signal(str)

    def __init__(self, view: QListView) -> None:
        super().__init__(view)
        self._view = view
        self._thumbs: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._loading: set[tuple] = set()
        self._failed: set[tuple] = set()  # videos that couldn't be read: not opened again until they change
        self._videos: set[tuple] = set()  # keys being read from a video
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._video_pool = QThreadPool(self)  # one video at a time, never in the way of snips' pictures
        self._video_pool.setMaxThreadCount(1)
        self._signals = _Loaded(self)
        self._signals.done.connect(self._thumb_ready)

    def clear_cache(self) -> None:
        self._thumbs.clear()
        self._failed.clear()

    # ------------------------------------------------------------------ geometry
    def sizeHint(self, _option, index) -> QSize:  # noqa: N802 - Qt API
        row = index.data(ROW_ROLE)
        if row is not None and row.is_header:
            return QSize(max(CARD_W, self._view.viewport().width() - 2), HEADER_H)
        return QSize(CARD_W + 2 * GAP, CARD_H + 2 * GAP)

    @staticmethod
    def card_rect(cell) -> QRectF:
        return QRectF(cell).adjusted(GAP, GAP, -GAP, -GAP)

    def star_rect(self, cell) -> QRectF:
        card = self.card_rect(cell)
        return QRectF(card.right() - 32, card.top() + THUMB_H + 10, 22, 22)

    def copy_rect(self, cell) -> QRectF:
        card = self.card_rect(cell)
        return QRectF(card.right() - 40, card.top() + 8, 32, 32)

    # ------------------------------------------------------------------ painting
    def paint(self, p: QPainter, option, index) -> None:
        row = index.data(ROW_ROLE)
        if row is None:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if row.is_header:
            self._paint_header(p, option, row)
        else:
            self._paint_card(p, option, row.item)
        p.restore()

    def _paint_header(self, p: QPainter, option, row: Row) -> None:
        t = theme.current()
        font = QFont(option.font)
        font.setPointSizeF(option.font.pointSizeF() * 1.0)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(t.palette.text2))
        p.drawText(QRectF(option.rect).adjusted(GAP, 14, -GAP, 0), Qt.AlignLeft | Qt.AlignTop, row.label)

    def _paint_card(self, p: QPainter, option, item: Item) -> None:
        t = theme.current()
        pal = t.palette
        card = self.card_rect(option.rect)
        hovered = bool(option.state & QStyle.State_MouseOver)
        selected = bool(option.state & QStyle.State_Selected)
        shape = QPainterPath()
        shape.addRoundedRect(card, RADIUS, RADIUS)
        p.fillPath(shape, QColor(pal.surface))

        thumb_area = QRectF(card.left(), card.top(), card.width(), THUMB_H)
        p.save()
        clip = QPainterPath()
        clip.addRect(thumb_area)
        p.setClipPath(shape.intersected(clip))
        p.fillRect(thumb_area, QColor(pal.surface2))
        pix = self._thumb(item, thumb_area.size().toSize(), self._view.devicePixelRatioF())
        if pix is not None:
            p.drawPixmap(thumb_area.topLeft(), pix)
        if item.entry.is_recording:
            self._paint_video_marks(p, thumb_area, item)
        p.restore()
        p.setPen(QPen(QColor(pal.border), 1))
        p.drawLine(QPointF(card.left(), card.top() + THUMB_H), QPointF(card.right(), card.top() + THUMB_H))

        meta = item.meta
        title_font = QFont(option.font)
        title_font.setBold(True)
        p.setFont(title_font)
        p.setPen(QColor(pal.text))
        text_left, text_width = card.left() + 12, card.width() - 52
        title = QFontMetrics(title_font).elidedText(meta.title or ("Recording" if item.entry.is_recording else "Snip"),
                                                    Qt.ElideRight, int(text_width))
        p.drawText(QRectF(text_left, card.top() + THUMB_H + 10, text_width, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   title)
        sub_font = QFont(option.font)
        sub_font.setPointSizeF(option.font.pointSizeF() * 0.9)
        p.setFont(sub_font)
        p.setPen(QColor(pal.text3))
        line = " · ".join(x for x in (meta.app, when_text(item.created)) if x)
        if meta.tags:
            line += "  ·  " + ", ".join(f"#{tag}" for tag in meta.tags[:3])
        line = QFontMetrics(sub_font).elidedText(line, Qt.ElideRight, int(card.width() - 24))
        p.drawText(QRectF(text_left, card.top() + THUMB_H + 32, card.width() - 24, 20),
                   Qt.AlignLeft | Qt.AlignVCenter, line)

        star = self.star_rect(option.rect).adjusted(4, 4, -4, -4)
        if meta.favorite:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(STAR_COLOR))
        else:
            p.setPen(QPen(QColor(pal.text2 if hovered else pal.text3), 1.4))  # visible (3:1) even unhovered
            p.setBrush(Qt.NoBrush)
        p.drawPolygon(_star(star))

        if hovered:
            chip = self.copy_rect(option.rect)
            p.setPen(QPen(QColor(pal.border), 1))
            p.setBrush(QColor(pal.surface))
            p.drawRoundedRect(chip, 8, 8)
            icon("copy").paint(p, chip.adjusted(7, 7, -7, -7).toRect())

        if selected or (option.state & QStyle.State_HasFocus and self._view.hasFocus()):
            p.setPen(QPen(QColor(t.accent.fill), 2))
        elif hovered:
            p.setPen(QPen(QColor(pal.text3), 1))
        else:
            p.setPen(QPen(QColor(pal.border), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(shape)

    @staticmethod
    def duration_text(item: Item) -> str:
        return duration_label(item)

    def _paint_video_marks(self, p: QPainter, thumb: QRectF, item: Item) -> None:
        """A play badge in the middle and the length at the bottom right: it's a video."""
        centre = thumb.center()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawEllipse(centre, 18, 18)
        p.setBrush(QColor("#ffffff"))
        p.drawPolygon(QPolygonF([QPointF(centre.x() - 6, centre.y() - 9), QPointF(centre.x() + 10, centre.y()),
                                 QPointF(centre.x() - 6, centre.y() + 9)]))
        text = self.duration_text(item)
        if text:
            font = QFont(p.font())
            font.setBold(True)
            p.setFont(font)
            width = QFontMetrics(font).horizontalAdvance(text) + 12
            chip = QRectF(thumb.right() - width - 8, thumb.bottom() - 26, width, 20)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(chip, 6, 6)
            p.setPen(QColor("#ffffff"))
            p.drawText(chip, Qt.AlignCenter, text)

    def _thumb(self, item: Item, size: QSize, ratio: float) -> QPixmap | None:
        """The card's picture if it's ready; otherwise starts loading it and returns None (the card
        shows a plain placeholder for a moment)."""
        path = item.entry.preview_path
        video = item.entry.is_recording and not path.exists()  # its first frame, made into a preview
        if video:
            path = item.entry.recording_path
        try:
            info = path.stat()
        except OSError:
            return None
        key = (item.entry.id, info.st_mtime_ns, size.width(), size.height(), ratio)
        cached = self._thumbs.get(key)
        if cached is not None:
            self._thumbs.move_to_end(key)
            return cached
        if key in self._loading or key in self._failed:
            return None
        if video and _cloud_only(info):  # OneDrive "online only": not downloading a whole video for a picture
            return None
        self._loading.add(key)
        target = QSize(round(size.width() * ratio), round(size.height() * ratio))
        preview = item.entry.path / "preview.png"
        make_preview = None if path == preview else str(preview)
        job = _ThumbJob(key, str(path), target, make_preview, self._signals, video=video)
        if video:
            self._videos.add(key)
            self._video_pool.start(job)
        else:
            self._pool.start(job)
        return None

    def _thumb_ready(self, key: tuple, image) -> None:
        self._loading.discard(key)
        video = key in self._videos
        self._videos.discard(key)
        if image is None:
            if video:  # not on every repaint: a broken video would be opened again and again
                self._failed.add(key)
            return
        pix = QPixmap.fromImage(image)
        pix.setDevicePixelRatio(key[4])
        self._thumbs[key] = pix
        while len(self._thumbs) > THUMB_CACHE:
            self._thumbs.popitem(last=False)
        self._view.viewport().update()

    # ------------------------------------------------------------------ clicks
    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            row = index.data(ROW_ROLE)
            if row is not None and not row.is_header:
                pos = event.position()
                if self.star_rect(option.rect).contains(pos):
                    self.favorite_clicked.emit(row.item.entry.id)
                    return True
                if self.copy_rect(option.rect).contains(pos):
                    self.copy_clicked.emit(row.item.entry.id)
                    return True
        return super().editorEvent(event, model, option, index)


class SnipGrid(QListView):
    """Cards flow left to right and wrap; each date header takes a whole line."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SnipGrid")
        self.setAccessibleName("Snips")
        self.setViewMode(QListView.ListMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.Adjust)
        self.setUniformItemSizes(False)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setFrameShape(QFrame.NoFrame)
        self.viewport().setAttribute(Qt.WA_Hover, True)
        self.model_ = LibraryModel(self)
        self.setModel(self.model_)
        self.delegate = CardDelegate(self)
        self.setItemDelegate(self.delegate)

    def resizeEvent(self, e) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(e)
        self.doItemsLayout()  # headers are as wide as the view

    def selected_ids(self) -> list[str]:
        rows = sorted(i.row() for i in self.selectionModel().selectedIndexes())
        return [self.model_.data(self.model_.index(r), ROW_ROLE).item.entry.id for r in rows]

    def restore(self, current: str | None, selected: list[str], scroll: int) -> None:
        """After the rows were rebuilt: put the selection, keyboard position and scroll back."""
        model, selection = self.model_, self.selectionModel()
        for entry_id in selected:
            row = model.row_for(entry_id)
            if row >= 0:
                selection.select(model.index(row), QItemSelectionModel.Select)
        row = model.row_for(current) if current else -1
        if row >= 0:
            selection.setCurrentIndex(model.index(row), QItemSelectionModel.NoUpdate)
        self.doItemsLayout()
        self.verticalScrollBar().setValue(scroll)

    def current_id(self) -> str | None:
        row = self.currentIndex().data(ROW_ROLE)
        return row.item.entry.id if row is not None and not row.is_header else None

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Asks for the menu (customContextMenuRequested, in viewport coordinates): where you
        right-clicked, or for the menu key (or Shift+F10) the snip the keyboard is on, scrolled into
        view and opened beside it, never a date header or whatever is under the middle of the grid.
        (The grid's policy is DefaultContextMenu, so only these events reach Python.)"""
        if event.reason() != QContextMenuEvent.Keyboard:
            self.customContextMenuRequested.emit(event.pos())
        elif self.current_id() is not None:
            self.scrollTo(self.currentIndex())
            self.customContextMenuRequested.emit(self.visualRect(self.currentIndex()).center())
        event.accept()
