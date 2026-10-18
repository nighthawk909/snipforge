"""Background work for the video editor (snipforge/video/tasks.py): results, progress and errors
come back on the UI thread; cancelling stops work that checks for it, and never hides a result."""
import threading
import time

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from snipforge.video.tasks import Task


class Heard:
    """Records each callback with the thread it ran on."""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    def __getattr__(self, name):
        return lambda *args: self.events.append((name, args, QThread.currentThread()))

    def names(self) -> list[str]:
        return [e[0] for e in self.events]


def run(task: Task, heard: Heard, timeout: float = 5.0) -> None:
    task.start()
    assert task.wait(timeout)
    end = time.perf_counter() + timeout
    while not any(n in heard.names() for n in ("done", "failed", "cancelled")):
        assert time.perf_counter() < end, "no result came back"
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()


def task_for(work, heard: Heard) -> Task:
    return Task(work, done=heard.done, failed=heard.failed, cancelled=heard.cancelled, progress=heard.progress,
                item=heard.item)


def test_results_progress_and_items_arrive_on_the_ui_thread(qapp):
    heard = Heard()

    def work(task):
        task.report(0.5)
        task.emit("thumbnail")
        return 42

    run(task_for(work, heard), heard)

    assert heard.events == [("progress", (0.5,), qapp.thread()), ("item", ("thumbnail",), qapp.thread()),
                            ("done", (42,), qapp.thread())]


def test_an_error_is_reported_with_its_message(qapp):
    heard = Heard()

    def work(task):
        raise OSError("The disk is full")

    run(task_for(work, heard), heard)

    assert heard.events[-1][:2] == ("failed", ("The disk is full",))


def test_work_that_checks_stops_when_cancelled(qapp):
    heard = Heard()
    started = threading.Event()

    def work(task):
        started.set()
        while True:
            task.check()
            time.sleep(0.005)

    task = task_for(work, heard).start()
    assert started.wait(5)
    task.cancel()

    assert task.wait(5)
    end = time.perf_counter() + 5
    while "cancelled" not in heard.names():
        assert time.perf_counter() < end, "no result came back"
        QApplication.processEvents()
    assert heard.names() == ["cancelled"]


def test_an_error_after_cancelling_counts_as_cancelled(qapp):
    heard = Heard()

    def work(task):
        task.cancel()
        raise RuntimeError("stopped part way, as asked")

    run(task_for(work, heard), heard)

    assert heard.names() == ["cancelled"]


def test_work_that_finishes_despite_a_cancel_is_done(qapp):
    """Cancel came too late to stop it: what it made exists, so it's reported as done."""
    heard = Heard()

    def work(task):
        task.cancel()
        return "saved.gif"

    run(task_for(work, heard), heard)

    assert heard.names() == ["done"]


@pytest.mark.parametrize("slow", [0.0, 0.05])
def test_wait_says_whether_the_work_ended(qapp, slow):
    release = threading.Event()
    task = Task(lambda t: release.wait(5)).start()

    assert not task.wait(slow)
    release.set()
    assert task.wait(5)
    assert not task.is_running
