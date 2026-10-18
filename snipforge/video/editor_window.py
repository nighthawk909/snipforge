"""The video editor: play a recording, trim it to the frame, and save a trimmed copy or a GIF.

Pictures come from two places. While playing, from Qt's player (player.py). While paused or
scrubbing, from FFmpeg directly (frames.read), which is exact to the frame and doesn't depend on
the player's timing; the player is only asked to play. Trimming and export don't use the player at
all, so they work even if a video won't play here. What's written goes next to the original under a
new name; the original is never changed.

    python -m snipforge.video.editor_window some.mp4

opens one for trying out. open_video(path) is the entry point for the rest of SnipForge.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from . import editor_parts, ffmpeg, frame_times, gif, trim
from .player import Player
from .tasks import Task
from .timeline import format_time
from .timeline_strip import TimelineStrip
from .video_view import VideoView

log = logging.getLogger(__name__)

CLOSE_WAIT = 5.0  # seconds, in all, to let background work finish when the window closes
GIF_FPS, GIF_WIDTH = 12, 960


class VideoEditorWindow(QMainWindow):
    def __init__(self, path: str | os.PathLike, *, player=None, trim_copy=trim.copy, export_gif=gif.export_gif,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = Path(os.path.abspath(path))
        self.frames = frame_times.read(self.path)  # FFmpegError if it isn't a video we can read
        self._trim_copy, self._export_gif = trim_copy, export_gif
        self._playing = False
        self._play_from = 0  # the frame playing started at; earlier ones the player sends aren't shown
        self._job: Task | None = None
        self._job_cancellable = False
        self._job_target = self.path
        self._saved: Path | None = None  # the last file written, for "Show in folder"
        self._stopped = False
        self.setWindowTitle(f"{self.path.name} - SnipForge")
        self.resize(1100, 720)
        self._build()
        self.player = player if player is not None else Player(self)
        self.player.frame_shown.connect(self._on_player_frame)
        self.player.finished.connect(self._on_player_finished)
        self.player.failed.connect(self._on_player_failed)
        self.player.load(self.path)
        self._stills = editor_parts.StillFrames(self.path, self.frames, self)
        self._stills.shown.connect(self._on_still)
        self._stills.failed.connect(self._on_still_failed)
        self._thumbs = Task(editor_parts.thumbnail_work(self.path, self.frames.end), item=self._on_thumbnail,
                            failed=self._on_thumbnails_failed, name="snipforge-video-thumbnails").start()
        QApplication.instance().aboutToQuit.connect(self._stop_work)  # a tray Quit doesn't close windows
        theme.on_change(self._restyle)
        self._refresh()
        self._restyle()
        self._stills.request(0)

    # ------------------------------------------------------------------ building
    def _build(self) -> None:
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._command_bar())
        self.view = VideoView()
        column.addWidget(self.view, 1)
        controls = QWidget()
        controls.setProperty("sfRole", "surface")
        inner = QVBoxLayout(controls)
        inner.setContentsMargins(16, 10, 16, 10)
        inner.addLayout(self._transport_row())
        self.timeline = TimelineStrip(self.frames)
        self.timeline.range_edited.connect(self._on_edited)
        self.timeline.playhead_edited.connect(self._on_edited)
        self.timeline.play_toggled.connect(self.toggle_play)
        inner.addWidget(self.timeline)
        inner.addLayout(self._status_row())
        column.addWidget(controls)
        self.setCentralWidget(body)
        self.timeline.setFocus()

    def _command_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("CommandBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 8, 12, 8)
        title = QLabel(self.path.name)
        title.setProperty("sfRole", "heading")
        row.addWidget(title, 1)
        self.gif_button = self._button("Export GIF", self.export_gif, "Make a GIF of the part you're keeping",
                                       "outline")
        self.save_button = self._button("Save trimmed copy", self.save_trimmed_copy,
                                        "Save the part you're keeping as a new video next to this one", "primary")
        row.addWidget(self.gif_button)
        row.addWidget(self.save_button)
        return bar

    def _transport_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.play_button = self._button("", self.toggle_play, "Play (Space)", "ghost")
        self.play_button.setIconSize(QSize(20, 20))
        self.play_button.setAccessibleName("Play")
        self.time_label = QLabel()
        self.range_label = QLabel()
        self.range_label.setProperty("sfRole", "secondary")
        row.addWidget(self.play_button)
        row.addWidget(self.time_label)
        row.addStretch(1)
        row.addWidget(self.range_label)
        return row

    def _status_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setProperty("sfRole", "secondary")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.reveal_button = self._button("Show in folder", self._reveal, "Show the saved file in its folder", "ghost")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFixedWidth(220)
        self.cancel_button = self._button("Cancel", self._cancel_job, "Stop making the GIF", None)
        for widget in (self.reveal_button, self.progress, self.cancel_button):
            widget.hide()
        row.addWidget(self.status_label, 1)
        for widget in (self.reveal_button, self.progress, self.cancel_button):
            row.addWidget(widget)
        return row

    @staticmethod
    def _button(text: str, slot, tip: str, variant: str | None) -> QPushButton:
        button = QPushButton(text)
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.TabFocus)  # a click leaves the keys with the timeline
        if variant:
            button.setProperty("variant", variant)
        button.clicked.connect(slot)
        return button

    def _restyle(self, _theme=None) -> None:
        colour = theme.current().palette.text
        self.play_button.setIcon(editor_parts.glyph("pause" if self._playing else "play", colour))

    # ------------------------------------------------------------------ state shown
    @property
    def is_playing(self) -> bool:
        return self._playing

    def _refresh(self) -> None:
        r, head = self.timeline.range, self.timeline.playhead
        self.time_label.setText(f"{format_time(self.frames.boundary(head))} / {format_time(self.frames.end)}")
        self.range_label.setText("Drag the handles to choose what to keep" if r.is_whole else
                                 f"Keeping {format_time(r.start)} to {format_time(r.end)} ({format_time(r.seconds)})")
        busy = self._job is not None
        self.save_button.setEnabled(not busy and not r.is_whole)
        self.gif_button.setEnabled(not busy)

    def _set_playing(self, playing: bool) -> None:
        self._playing = playing
        self.play_button.setToolTip("Pause (Space)" if playing else "Play (Space)")
        self.play_button.setAccessibleName("Pause" if playing else "Play")
        self._restyle()
        self._refresh()

    def _set_status(self, text: str, error: bool = False, saved: Path | None = None) -> None:
        self.status_label.setText(text)
        self.status_label.setProperty("sfRole", "error" if error else "secondary")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self._saved = saved
        self.reveal_button.setVisible(saved is not None)

    # ------------------------------------------------------------------ playing
    def toggle_play(self) -> None:
        if self._playing:
            self._pause()
            return
        r, head = self.timeline.range, self.timeline.playhead
        start = head if r.first <= head < r.last else r.first  # from the in point when at or past the end
        self.timeline.set_playhead(start)
        self._play_from = start
        self._set_playing(True)
        self.player.play((self.frames.boundary(start) + self.frames.boundary(start + 1)) / 2)  # mid-frame

    def _pause(self) -> None:
        self.player.pause()
        self._set_playing(False)

    def _on_player_frame(self, image: QImage, seconds: float) -> None:
        index, r = self.frames.index_at(seconds), self.timeline.range
        if not self._playing or index < self._play_from:
            return  # late, or from before where playing started (Qt's player can start a little early)
        if index >= r.stop:  # reached the out point: stop on the last frame kept
            self._pause()
            self.timeline.set_playhead(r.last)
            self._refresh()
            self._stills.request(r.last)
            return
        self.view.set_image(image)
        self.timeline.set_playhead(index)
        self.time_label.setText(f"{format_time(self.frames.boundary(index))} / {format_time(self.frames.end)}")

    def _on_player_finished(self) -> None:
        self._set_playing(False)

    def _on_player_failed(self, message: str) -> None:
        self._set_playing(False)
        self._set_status(message, error=True)

    # ------------------------------------------------------------------ editing
    def _on_edited(self, _value=None) -> None:
        if self._playing:
            self._pause()
        self._refresh()
        self._stills.request(self.timeline.playhead)

    def _on_still(self, _index: int, image: QImage) -> None:
        if not self._playing:
            self.view.set_image(image)

    def _on_still_failed(self, message: str) -> None:
        self._set_status(f"Couldn't show that frame: {message}", error=True)

    def _on_thumbnail(self, item) -> None:
        seconds, image = item
        self.timeline.add_thumbnail(seconds, image)

    def _on_thumbnails_failed(self, message: str) -> None:
        self._set_status(f"Couldn't make the timeline's pictures: {message}", error=True)

    # ------------------------------------------------------------------ saving
    def save_trimmed_copy(self) -> None:
        r = self.timeline.range
        dst = editor_parts.next_free_name(self.path, "trimmed", ".mp4")
        self._start_job(lambda task: self._trim_copy(self.path, dst, start=r.start, end=r.export_end), dst,
                        "Saving the trimmed copy…")

    def export_gif(self) -> None:
        r = self.timeline.range
        dst = editor_parts.next_free_name(self.path, None, ".gif")

        def work(task):
            return self._export_gif(self.path, dst, start=r.start, end=r.export_end, fps=GIF_FPS, width=GIF_WIDTH,
                                    progress=task.report, cancel=lambda: task.is_cancelled)
        self._start_job(work, dst, "Making the GIF…", cancellable=True)

    def _start_job(self, work, dst: Path, message: str, cancellable: bool = False) -> None:
        """One save or export at a time, on its own thread; its buttons wait until it's done. A
        cancellable one (the GIF) shows its progress and a Cancel button."""
        if self._job is not None:
            return
        if self._playing:
            self._pause()
        self._set_status(message)
        self.progress.setValue(0)
        self.progress.setVisible(cancellable)
        self.cancel_button.setVisible(cancellable)
        self._job_target, self._job_cancellable = dst, cancellable
        self._job = Task(lambda task: work(task) or dst, done=self._on_job_done, failed=self._on_job_failed,
                         cancelled=self._on_job_cancelled, progress=self._on_job_progress,
                         name="snipforge-video-save").start()
        self._refresh()

    def _on_job_progress(self, fraction: float) -> None:
        self.progress.setValue(round(fraction * 100))

    def _on_job_done(self, saved) -> None:
        self._end_job()
        self._set_status(f"Saved “{Path(saved).name}” next to the original.", saved=Path(saved))

    def _on_job_failed(self, message: str) -> None:
        self._end_job()
        self._set_status(f"Couldn't save “{self._job_target.name}”: {message}", error=True)

    def _on_job_cancelled(self) -> None:
        self._end_job()
        self._set_status("GIF export cancelled. Nothing was saved.")

    def _cancel_job(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self.cancel_button.setEnabled(False)

    def _end_job(self) -> None:
        self._job = None
        self.progress.hide()
        self.cancel_button.hide()
        self.cancel_button.setEnabled(True)
        self._refresh()

    def _reveal(self) -> None:
        if self._saved is not None and not editor_parts.reveal(self._saved):
            self._set_status(f"Couldn't open the folder of “{self._saved.name}”.", error=True, saved=self._saved)

    # ------------------------------------------------------------------ keys and closing
    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self.timeline.handle_key(event):  # keys the timeline didn't get because something else had focus
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._job is not None and self._job_cancellable and not self._stop_making_gif():
            event.ignore()
            return
        self._stop_work()
        super().closeEvent(event)

    def _stop_making_gif(self) -> bool:
        answer = QMessageBox.question(self, "SnipForge", "The GIF isn't finished yet. Stop making it and close?",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def _stop_work(self) -> None:
        """Cancel what can be cancelled (thumbnails, still frames, a GIF, which then removes its
        unfinished file) and give it all, with a trimmed copy being saved, a few seconds to end."""
        if self._stopped:
            return
        self._stopped = True
        tasks = [t for t in (self._thumbs, self._stills.task, self._job) if t is not None]
        for task in tasks:
            if task is not self._job or self._job_cancellable:
                task.cancel()
        deadline = time.monotonic() + CLOSE_WAIT
        for task in tasks:
            if not task.wait(max(0.0, deadline - time.monotonic())):
                log.warning("Video editor work for %s was still running as the editor closed", self.path.name)
        self._playing = False
        self.player.close()


def open_video(path: str | os.PathLike, parent: QWidget | None = None) -> VideoEditorWindow:
    """Open `path` in a new editor window and show it. Raises FFmpegError if it can't be read.

    Without a parent the window belongs to the caller: keep a reference to it while it's open (Python
    deletes a window nothing refers to)."""
    window = VideoEditorWindow(path, parent=parent)
    window.show()
    window.raise_()
    window.activateWindow()
    return window


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage: python -m snipforge.video.editor_window <video.mp4>", file=sys.stderr)
        return 2
    app = QApplication.instance()
    if app is None:  # run on its own: dress it like SnipForge (a running SnipForge already is)
        app = QApplication(sys.argv[:1])
        theme.install(app)
    try:
        window = open_video(args[0])
    except (OSError, ValueError, ffmpeg.FFmpegError) as exc:
        QMessageBox.critical(None, "SnipForge", f"Couldn't open {Path(args[0]).name}: {exc}")
        return 1
    window.setAttribute(Qt.WA_QuitOnClose, True)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
