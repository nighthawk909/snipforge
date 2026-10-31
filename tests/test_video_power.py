"""snipforge/video/power.py: the face the recorder process uses, the same on every system. Windows'
messages are tested in test_video_power_win32.py, and sent for real in the desktop suite."""
from snipforge.video import power


def test_where_it_cant_watch_yet_it_does_nothing_and_never_fails(off_windows, qapp):
    off_windows(power)
    watch = power.SessionWatch()
    heard = []
    watch.sleeping.connect(lambda: heard.append("sleep"))

    watch.block_shutdown("Saving your screen recording")
    watch.allow_shutdown()
    watch.close()

    assert watch.window_id == 0 and heard == []
    assert power.stay_awake(True) is False


def test_events_become_signals(off_windows, qapp):
    off_windows(power)  # no window: the events are handed in directly
    watch = power.SessionWatch()
    heard = []
    for name in ("sleeping", "ending_soon", "ending", "not_ending", "display_off", "display_on", "locked",
                 "unlocked"):
        getattr(watch, name).connect(lambda name=name: heard.append(name))

    for event in ("sleep", "ending?", "ending", "not ending", "display off", "display dimmed", "display on",
                  "locked", "unlocked"):
        watch.handle(event)

    assert heard == ["sleeping", "ending_soon", "ending", "not_ending", "display_off", "display_on", "locked",
                     "unlocked"]  # dimmed: nothing


def test_the_face_imports_nothing_windows_only_at_the_top():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(power))
    imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    names = {alias.name for node in imports if isinstance(node, ast.Import) for alias in node.names}
    names |= {node.module for node in imports if isinstance(node, ast.ImportFrom) and node.module}
    assert {name.split(".")[0] for name in names} <= {"__future__", "logging", "sys", "PySide6"}, names


class FakeOs:
    """Stands in for a platform's half (_power_win32.Listener)."""

    def __init__(self) -> None:
        self.calls = []

    def block_shutdown(self, reason):
        self.calls.append(("block", reason))

    def allow_shutdown(self):
        self.calls.append(("allow",))

    def close(self):
        self.calls.append(("close",))


def test_the_face_hands_shutdown_and_closing_to_the_platforms_half(off_windows, qapp):
    off_windows(power)
    watch = power.SessionWatch()
    half = watch._os = FakeOs()
    watch.window_id = 4242

    watch.block_shutdown("Saving your screen recording")
    watch.allow_shutdown()
    watch.close()
    watch.close()  # twice: nothing more

    assert half.calls == [("block", "Saving your screen recording"), ("allow",), ("close",)]
    assert watch._os is None and watch.window_id == 0
    watch.block_shutdown("after closing")  # no half left: nothing happens, nothing fails
