"""The video editor's edits, for VideoEditorWindow: the edit (edit.VideoEdit) with undo and redo,
cutting out the part chosen on the timeline (X) and putting a cut back (Delete in it), the title and
caption, playing past cuts, and saving: a plain trim is copied (fast, nothing lost), anything more is
encoded again (render.Render), which shows its progress and can be cancelled.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QAction, QImage, QKeySequence

from . import edit_dialogs, editor_parts
from .edit import VideoEdit, with_caption
from .timeline import format_time

UNDO_LIMIT = 100


class EditsMixin:
    """Uses the window's timeline, view, player, buttons, labels and _start_job / _set_status."""

    edit: VideoEdit

    def _build_edits(self) -> None:
        self.edit = VideoEdit.whole(self.frames)
        self._undo: list[VideoEdit] = []
        self._redo: list[VideoEdit] = []
        self._last_change = ""  # a drag's many small range changes are one undo step
        self._render = None
        for keys, slot in (("X", self.cut_selection), ("Delete", self.put_back), ("Backspace", self.put_back),
                           (QKeySequence.Undo, self.undo), (QKeySequence.Redo, self.redo),
                           ("Ctrl+Y", self.redo)):
            action = QAction(self)
            action.setShortcut(QKeySequence(keys))
            action.triggered.connect(slot)
            self.addAction(action)
        self.timeline.selection_changed.connect(lambda _s: self._refresh())

    # ------------------------------------------------------------------ changing the edit
    def _apply(self, edit: VideoEdit, change: str = "") -> None:
        if edit == self.edit:
            return
        if not (change and change == self._last_change and self._undo):
            self._undo = (self._undo + [self.edit])[-UNDO_LIMIT:]
        self._redo = []
        self._last_change = change
        self._show_edit(edit)

    def _show_edit(self, edit: VideoEdit) -> None:
        self.edit = edit
        if self.timeline.range != edit.range:
            self.timeline.set_range(edit.range)
        self.timeline.set_cuts(edit.cuts)
        self._refresh()
        self._stills.request(self.timeline.playhead)

    def undo(self) -> None:
        if self._undo and self._job is None:
            self._redo.append(self.edit)
            self._last_change = ""
            self._show_edit(self._undo.pop())

    def redo(self) -> None:
        if self._redo and self._job is None:
            self._undo.append(self.edit)
            self._last_change = ""
            self._show_edit(self._redo.pop())

    def _range_edited(self, trim_range) -> None:
        self._apply(self.edit.with_range(trim_range), change="range")

    def cut_selection(self) -> None:
        selection = self.timeline.selection
        if selection is None or self._job is not None:
            return
        try:
            edited = self.edit.with_cut(*selection)
        except ValueError as exc:
            self._set_status(f"Couldn't cut that out: {exc}.", error=True)
            return
        self.timeline.set_selection(None)
        if edited == self.edit:
            self._set_status("That part is already left out.")
        self._apply(edited)

    def put_back(self) -> None:
        cut = self.edit.cut_at(self.timeline.playhead)
        if cut is not None and self._job is None:
            self._apply(self.edit.without_cut(cut))

    def edit_title(self) -> None:
        image = self.view.image
        size = image.size() if image is not None and not image.isNull() else self._frame_size()
        answer = edit_dialogs.ask_title(self, self.edit.title, size)
        if answer is not None:
            self._apply(self.edit.with_title(answer[1]))

    def edit_caption(self) -> None:
        answer = edit_dialogs.ask_caption(self, self.edit.caption)
        if answer is not None:
            self._apply(self.edit.with_caption(answer[1] or ""))

    def _frame_size(self):
        """The video's own size, for the title card's preview before a frame is shown."""
        from PySide6.QtCore import QSize

        from . import ffmpeg
        try:
            video = ffmpeg.probe(self.path).first("video")
        except (OSError, ffmpeg.FFmpegError):
            video = None
        return QSize(video.width, video.height) if video is not None and video.width > 0 else QSize(1280, 720)

    # ------------------------------------------------------------------ showing it
    def _describe(self) -> str:
        e = self.edit
        if e.is_whole:
            return "Drag the handles to choose what to keep"
        parts = len(e.kept_spans())
        what = [f"{parts} parts" if parts > 1 else
                f"{format_time(e.range.start)} to {format_time(e.range.end)}"]
        if e.title:
            what.append(f"a {e.title.seconds:g} s title")
        if e.caption:
            what.append("a caption")
        return f"Keeping {', '.join(what)} ({format_time(e.seconds)})"

    def _frame_shown(self, image: QImage) -> QImage:
        """What the view shows for a frame: with the caption on it, as it will be saved."""
        return with_caption(image, self.edit.caption)

    def _skip_cut(self, index: int) -> bool:
        """Playing reached a part that's cut out: carry on after it. True if it did."""
        cut = self.edit.cut_at(index)
        if cut is None:
            return False
        after = cut[1]
        if after >= self.edit.range.stop:
            return False  # the cut runs to the end: playing stops there
        self._play_from = after
        self.timeline.set_playhead(after)
        self.player.play((self.frames.boundary(after) + self.frames.boundary(after + 1)) / 2)
        return True

    # ------------------------------------------------------------------ saving
    def save_video(self) -> None:
        if not self.edit.needs_render:
            r = self.edit.range
            dst = editor_parts.next_free_name(self.path, "trimmed", ".mp4")
            self._start_job(lambda task: self._trim_copy(self.path, dst, start=r.start, end=r.export_end), dst,
                            "Saving the trimmed copy…")
            return
        if self._job is not None or self._render is not None:
            return
        dst = editor_parts.next_free_name(self.path, "edited", ".mp4")
        if self._playing:
            self._pause()
        job = self._render_video(self.path, dst, self.edit, self)
        self._render = job
        job.progress.connect(self._on_job_progress)
        job.finished.connect(self._render_done)
        job.failed.connect(self._render_failed)
        job.cancelled.connect(self._render_cancelled)
        self._job_target, self._job_cancellable = dst, True
        self._job = job
        self._set_status("Saving the edited video…")
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_button.show()
        self._refresh()
        job.start()

    def _render_done(self, saved) -> None:
        self._render = None
        self._on_job_done(saved)

    def _render_failed(self, message: str) -> None:
        self._render = None
        self._on_job_failed(message)

    def _render_cancelled(self) -> None:
        self._render = None
        self._end_job()
        self._set_status("Saving cancelled. Nothing was saved.")

    def gif_work(self, dst: Path):
        r, edit = self.edit.range, self.edit
        if edit.needs_render:
            return lambda task: self._export_edited_gif(self.path, dst, edit, fps=self.GIF_FPS, width=self.GIF_WIDTH,
                                                        progress=task.report, cancel=lambda: task.is_cancelled)
        return lambda task: self._export_gif(self.path, dst, start=r.start, end=r.export_end, fps=self.GIF_FPS,
                                             width=self.GIF_WIDTH, progress=task.report,
                                             cancel=lambda: task.is_cancelled)
