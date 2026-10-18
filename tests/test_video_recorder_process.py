"""snipforge/video/remote_recorder.py and recorder_process.py: a recording made by the recorder
process, here played inside the test (a RecorderChild on a real channel, with a fake Recorder), so
everything said between SnipForge and the recorder is checked. The desktop suite runs the real
process."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal

from snipforge import __version__
from snipforge.video import recorder_process, remote_recorder
from snipforge.video.channel import Channel, connect, listen, new_name
from snipforge.video.recorder import ACTIVE, RecordingError, Result, Settings
from snipforge.video.recorder_process import RecorderChild, channel_name
from snipforge.video.remote_recorder import RemoteRecorder, ping


def wait_for(condition, seconds=5.0) -> bool:
    deadline = time.perf_counter() + seconds
    while not condition() and time.perf_counter() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    return bool(condition())


class FakeRecorder(QObject):
    """A Recorder that records nothing: "recording" at once, a Result 50 ms after stop."""

    state_changed = Signal(str)
    warning = Signal(str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings, self.state, self.calls = settings, "idle", []

    def start(self, hold=False):
        self.calls.append("start held" if hold else "start")
        self.held = hold
        if self.settings.output.stem == "refuse":
            raise RecordingError("No screen to record")
        if self.settings.output.stem == "slow":
            return  # opening the devices takes a while: nothing said yet
        self._set("starting")
        if not hold:
            QTimer.singleShot(0, self, lambda: (self._set("recording"), self.warning.emit("No microphone here")))

    def go(self):
        self.calls.append("go")
        if self.state == "starting" and self.held:
            self.held = False
            self._set("recording")

    def pause(self):
        self.calls.append("pause")
        self._set("paused")

    def resume(self):
        self.calls.append("resume")
        self._set("recording")

    def stop(self):
        self.calls.append("stop")
        if self.state in ACTIVE:
            self._set("finishing")
            QTimer.singleShot(50, self, self._finish)

    def cancel(self):
        self.calls.append("cancel")
        self._set("cancelled")
        self.cancelled.emit()

    def finish_now(self, timeout):
        self.calls.append(("finish_now", timeout))
        if self.state in ACTIVE:
            self._set("finishing")
        self._finish()
        return True

    def _finish(self):
        if self.state == "finishing" and self.settings.output.stem != "stuck":
            self._set("finished")
            stats = {"odd": {1, 2}} if self.settings.output.stem == "badstats" else {"frames_made": 37}
            self.finished.emit(Result(self.settings.output, 1.25, stats))

    def _set(self, state):
        self.state = state
        self.state_changed.emit(state)


class FakeWatch(QObject):
    sleeping = Signal()
    ending_soon = Signal()
    ending = Signal()
    not_ending = Signal()
    window_id = 4242

    def __init__(self):
        super().__init__()
        self.blocked, self.closed = None, False

    def block_shutdown(self, reason):
        self.blocked = reason

    def allow_shutdown(self):
        self.blocked = None

    def close(self):
        self.closed = True


class InProcess:
    """Plays the recorder process: launch() starts a RecorderChild on the channel, in this process."""

    def __init__(self):
        self.children, self.exits, self.watch = [], [], FakeWatch()

    def __call__(self, args):
        name = args[args.index("--record") + 1]

        def begin():
            channel = Channel(connect(name, 2000))
            self.children.append(RecorderChild(channel, self.watch, make_recorder=FakeRecorder, quit=self.exits.append))

        QTimer.singleShot(0, begin)
        return True

    @property
    def recorder(self):
        return self.children[0].recorder


def settings(tmp_path, name="rec"):
    return Settings(area=(0, 0, 320, 240), output=Path(tmp_path).resolve() / f"{name}.mp4")


def remote_for(tmp_path, name="rec", **kwargs):
    process = InProcess()
    remote = RemoteRecorder(settings(tmp_path, name), launch=process, **kwargs)
    seen = {"states": [], "warnings": [], "results": [], "failures": [], "cancelled": []}
    remote.state_changed.connect(seen["states"].append)
    remote.warning.connect(seen["warnings"].append)
    remote.finished.connect(seen["results"].append)
    remote.failed.connect(seen["failures"].append)
    remote.cancelled.connect(lambda: seen["cancelled"].append(True))
    return remote, process, seen


def test_a_recording_made_in_the_recorder_process(tmp_path, qapp):
    remote, process, seen = remote_for(tmp_path)

    remote.start()
    assert wait_for(lambda: remote.state == "recording")
    remote.pause()
    assert wait_for(lambda: remote.state == "paused")
    remote.resume()
    assert wait_for(lambda: remote.state == "recording")
    remote.stop()
    assert wait_for(lambda: seen["results"])

    assert seen["states"] == ["launching", "starting", "recording", "paused", "recording", "finishing", "finished"]
    result = seen["results"][0]
    assert (result.path, result.duration, result.stats) == (remote.settings.output, 1.25, {"frames_made": 37})
    assert seen["warnings"] == ["No microphone here"] and remote.ended_by is None
    assert process.recorder.settings == remote.settings  # the settings arrived as sent
    assert process.recorder.calls == ["start", "pause", "resume", "stop"]
    assert (remote.pid, remote.window_id) == (os.getpid(), 4242)
    assert process.exits == [0] and process.watch.blocked is None
    assert not process.watch.closed  # main() closes it, once Windows' callbacks are over


def test_a_recorder_that_cant_start_says_why(tmp_path, qapp):
    remote, process, seen = remote_for(tmp_path, name="refuse")

    remote.start()

    assert wait_for(lambda: seen["failures"])
    assert seen["failures"] == ["No screen to record"] and remote.state == "failed"
    assert wait_for(lambda: process.exits == [1])


def test_settings_the_recorder_cant_use_are_refused_there(tmp_path, qapp):
    name = new_name()
    server = listen(name)
    exits = []
    child = RecorderChild(Channel(connect(name, 2000)), FakeWatch(), make_recorder=FakeRecorder, quit=exits.append)
    assert server.waitForNewConnection(2000)
    snipforge = Channel(server.nextPendingConnection())
    heard = []
    snipforge.message.connect(heard.append)

    snipforge.send("start", settings={"output": "relative.mp4"})

    assert wait_for(lambda: len(heard) == 2)
    assert heard[0]["type"] == "hello" and heard[1]["type"] == "failed"
    assert "can't be saved" in heard[1]["message"] and exits == [1] and child.recorder is None
    server.close()


def test_a_recorder_that_cant_be_launched_stops_the_start(tmp_path, qapp):
    remote = RemoteRecorder(settings(tmp_path), launch=lambda args: False)

    with pytest.raises(RecordingError, match="couldn't start its recorder"):
        remote.start()


def test_a_recorder_that_never_calls_back_fails_the_recording(tmp_path, qapp):
    remote = RemoteRecorder(settings(tmp_path), launch=lambda args: True, connect_timeout_ms=200)
    failures = []
    remote.failed.connect(failures.append)

    remote.start()

    assert wait_for(lambda: failures == ["SnipForge's recorder didn't start"])


def test_stopping_before_the_recorder_calls_back_cancels(tmp_path, qapp):
    remote = RemoteRecorder(settings(tmp_path), launch=lambda args: True)
    cancelled = []
    remote.cancelled.connect(lambda: cancelled.append(True))
    remote.start()

    remote.stop()

    assert cancelled == [True] and remote.state == "cancelled"


def test_a_recorder_that_dies_mid_recording_fails_it(tmp_path, qapp):
    remote, process, seen = remote_for(tmp_path)
    remote.start()
    assert wait_for(lambda: remote.state == "recording")

    process.children[0].channel.socket.abort()  # the process is gone

    assert wait_for(lambda: seen["failures"] == ["SnipForge's recorder stopped unexpectedly"])


def test_snipforge_going_away_finishes_the_recording(tmp_path, qapp):
    remote, process, _seen = remote_for(tmp_path)
    remote.start()
    assert wait_for(lambda: remote.state == "recording")

    remote.channel.close()  # SnipForge quit or crashed

    assert wait_for(lambda: process.exits == [0])
    assert process.recorder.calls[-1] == "stop" and process.recorder.state == "finished"
    assert process.children[0].ended_by == "SnipForge closed"


def test_sleep_finishes_the_recording_before_windows_sleeps(tmp_path, qapp):
    remote, process, seen = remote_for(tmp_path)
    remote.start()
    assert wait_for(lambda: remote.state == "recording")

    process.watch.sleeping.emit()

    assert process.recorder.state == "finished"  # before the signal returned, so before Windows sleeps
    assert process.recorder.calls[-1] == ("finish_now", recorder_process.SLEEP_FINISH_SECONDS)
    assert wait_for(lambda: seen["results"]) and remote.ended_by == "sleep"


def test_the_session_ending_finishes_the_recording_while_windows_waits(tmp_path, qapp):
    remote, process, seen = remote_for(tmp_path)
    remote.start()
    assert wait_for(lambda: remote.state == "recording")

    process.watch.ending_soon.emit()
    assert process.watch.blocked == "Saving your screen recording" and process.recorder.state == "recording"
    process.watch.ending.emit()

    assert process.recorder.state == "finished" and process.watch.blocked is None
    assert process.recorder.calls[-1] == ("finish_now", recorder_process.END_FINISH_SECONDS)
    assert wait_for(lambda: seen["results"]) and remote.ended_by == "session end"


def test_a_report_of_some_other_file_is_refused(tmp_path, qapp):
    class Liar(InProcess):
        def __call__(self, args):
            name = args[args.index("--record") + 1]

            def begin():
                channel = Channel(connect(name, 2000))
                self.children.append(channel)
                channel.message.connect(lambda m: m["type"] == "start" and channel.send(
                    "finished", path="C:/Windows/system32/x.mp4", duration=1.0, stats={}))

            QTimer.singleShot(0, begin)
            return True

    remote = RemoteRecorder(settings(tmp_path), launch=Liar())
    failures = []
    remote.failed.connect(failures.append)
    remote.start()

    assert wait_for(lambda: failures == ["The recorder reported a recording SnipForge didn't ask for"])


def test_a_ping_proves_the_recorder_can_run(qapp):
    process = InProcess()

    assert ping(launch=process, timeout_ms=5000) == __version__
    assert wait_for(lambda: process.exits == [0])


def test_a_ping_nobody_answers_raises(qapp):
    with pytest.raises(RecordingError, match="didn't answer"):
        ping(launch=lambda args: True, timeout_ms=200)


GOOD_NAME = "SnipForge-recorder-" + "0123456789abcdef" * 2


@pytest.mark.parametrize("argv, expected", [
    (["SnipForge.exe", "--record", GOOD_NAME], GOOD_NAME),
    (["SnipForge.exe", "--record"], None),
    (["SnipForge.exe", "--record", "SomethingElse"], None),
    (["SnipForge.exe", "--record", "SnipForge-recorder-josh-0123abcd"], None),  # old style, with a user name
    (["SnipForge.exe", "--record", GOOD_NAME.upper()], None),
    (["SnipForge.exe", "--record", GOOD_NAME + "/x"], None),
    (["SnipForge.exe"], None),
])
def test_the_recorder_only_takes_channel_names_snipforge_makes(argv, expected):
    assert channel_name(argv) == expected


def test_the_recorder_needs_its_channel_name():
    assert recorder_process.main(["SnipForge.exe", "--record"]) == 2


def test_stopping_during_the_handshake_cancels_the_recorder_too(tmp_path, qapp):
    remote, process, seen = remote_for(tmp_path, name="slow")
    remote.start()
    assert wait_for(lambda: process.children and process.recorder is not None)
    assert remote.state == "launching"  # connected, told to start, nothing heard yet

    remote.stop()

    assert seen["cancelled"] == [True] and seen["failures"] == []
    assert wait_for(lambda: process.exits == [0])
    assert process.recorder.calls == ["start", "cancel"]  # the recorder didn't carry on alone


def test_a_recorder_that_breaks_while_reporting_still_exits(tmp_path, qapp):
    remote, process, seen = remote_for(tmp_path, name="badstats")
    remote.start()
    assert wait_for(lambda: remote.state == "recording")

    remote.stop()

    assert wait_for(lambda: process.exits == [0])  # the report couldn't be written; it ended anyway
    assert wait_for(lambda: seen["failures"] == ["SnipForge's recorder stopped unexpectedly"])


def test_a_ping_while_recording_is_ignored(tmp_path, qapp):
    remote, process, _seen = remote_for(tmp_path)
    remote.start()
    assert wait_for(lambda: remote.state == "recording")

    remote.channel.send("ping")
    wait_for(lambda: False, 0.2)

    assert process.exits == [] and process.recorder.state == "recording"


def test_a_recorder_that_never_finishes_fails_the_recording(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(remote_recorder, "FINISH_DEADLINE_MS", 300)
    remote, process, seen = remote_for(tmp_path, name="stuck")
    remote.start()
    assert wait_for(lambda: remote.state == "recording")

    remote.stop()

    assert wait_for(lambda: seen["failures"] == ["SnipForge's recorder didn't finish the recording"])


def test_a_recorder_that_dies_before_calling_back_is_noticed(tmp_path, qapp):
    gone = subprocess.Popen([sys.executable, "-c", ""])
    gone.wait()
    remote = RemoteRecorder(settings(tmp_path), launch=lambda args: gone.pid)
    failures = []
    remote.failed.connect(failures.append)

    remote.start()

    assert wait_for(lambda: failures == ["SnipForge's recorder stopped before it started"], 3.0)


def test_settings_the_recorder_would_refuse_stop_the_start_before_launching(qapp):
    launched = []
    remote = RemoteRecorder(Settings(area=(0, 0, 320, 240), output=Path("relative.mp4")),
                            launch=lambda args: launched.append(args) or True)

    with pytest.raises(RecordingError, match="can't be saved"):
        remote.start()
    assert launched == []


def test_a_launch_that_raises_is_a_recording_error(tmp_path, qapp):
    def explode(args):
        raise OSError("no such program")

    with pytest.raises(RecordingError, match="no such program"):
        RemoteRecorder(settings(tmp_path), launch=explode).start()


def test_a_process_that_has_exited_isnt_alive():
    gone = subprocess.Popen([sys.executable, "-c", ""])
    gone.wait()

    assert remote_recorder.process_alive(os.getpid())
    assert not remote_recorder.process_alive(gone.pid)


def test_a_held_recording_begins_when_snipforge_says_go(tmp_path, qapp):
    remote, process, _seen = remote_for(tmp_path)
    remote.start(hold=True)
    assert wait_for(lambda: remote.state == "starting")
    wait_for(lambda: False, 0.2)
    assert remote.state == "starting"  # held: devices open, nothing recorded

    remote.go()

    assert wait_for(lambda: remote.state == "recording")
    assert process.recorder.calls == ["start held", "go"]


def test_go_before_the_recorder_has_called_back_still_counts(tmp_path, qapp):
    remote, process, _seen = remote_for(tmp_path)
    remote.start(hold=True)

    remote.go()  # the countdown ended before the recorder process was up

    assert wait_for(lambda: remote.state == "recording")
    assert process.recorder.calls == ["start"]
