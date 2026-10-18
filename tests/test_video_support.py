"""snipforge/video/support.py and disk_space.py: what the computer and the drive must have to record."""
import sys
import threading
import time

import pytest

from snipforge.video import disk_space, support


class WindowsSys:
    platform = "win32"


def test_windows_without_media_foundation_is_told_what_to_add(monkeypatch):
    monkeypatch.setattr(support, "sys", WindowsSys())
    monkeypatch.setattr(support, "_loads", lambda name: name != "mfplat")

    reason = support.unsupported_reason()

    assert "Media Feature Pack" in reason and "Optional features" in reason


def test_windows_with_media_foundation_can_record(monkeypatch):
    monkeypatch.setattr(support, "sys", WindowsSys())
    monkeypatch.setattr(support, "_loads", lambda name: True)

    assert support.unsupported_reason() is None


@pytest.mark.skipif(sys.platform != "win32", reason="loads Windows' own DLLs")
def test_only_windows_own_libraries_are_looked_for():
    assert support._loads("kernel32")
    assert not support._loads("snipforge_no_such_library")


def test_free_space_is_read_for_this_user(tmp_path):
    assert disk_space.free_bytes(tmp_path) > 0


def test_a_folder_that_isnt_there_cant_be_read(tmp_path):
    with pytest.raises(OSError):
        disk_space.free_bytes(tmp_path / "missing")


def test_space_is_read_again_and_again_on_a_thread_of_its_own(tmp_path):
    threads = []

    def read(folder):
        threads.append(threading.current_thread().name)
        return 123

    watch = disk_space.SpaceWatch(tmp_path, read, seconds=0.05)
    started = time.perf_counter()
    watch.start()
    deadline = started + 5.0
    while watch.reads < 3 and time.perf_counter() < deadline:  # a busy machine's thread comes late
        time.sleep(0.01)
    watch.stop()
    took = time.perf_counter() - started

    assert watch.latest == 123 and 3 <= watch.reads <= took / 0.05 + 2  # again and again, not flat out
    assert set(threads) == {"disk-space"}
    assert not any(t.name == "disk-space" and t.is_alive() for t in threading.enumerate())


def test_a_drive_that_cant_be_read_is_unknown_not_full(tmp_path):
    answers = iter([500, OSError(21, "The device is not ready")])

    def read(folder):
        answer = next(answers, 700)
        if isinstance(answer, Exception):
            raise answer
        return answer

    watch = disk_space.SpaceWatch(tmp_path, read, seconds=0.05)
    watch.start()
    seen = set()
    deadline = time.perf_counter() + 5
    while time.perf_counter() < deadline and 700 not in seen:
        seen.add(watch.latest)
        time.sleep(0.005)
    watch.stop()

    assert {500, None, 700} <= seen  # the unreadable moment was None, and reading went on
