"""snipforge/video/channel.py: messages between SnipForge and its recorder process."""
import json
import os
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalServer

from snipforge.video import channel as ch
from snipforge.video.cursor import PointerEffects
from snipforge.video.recorder import Settings

OUTPUT = str(Path.cwd().resolve() / "rec.mp4")


def wait_for(condition, seconds=3.0) -> bool:
    deadline = time.perf_counter() + seconds
    while not condition() and time.perf_counter() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    return bool(condition())


@pytest.fixture
def pair(qapp):
    """Two connected channels: (SnipForge's end, the recorder's end)."""
    name = ch.new_name()
    server = ch.listen(name)
    far = ch.Channel(ch.connect(name, 2000))
    assert server.waitForNewConnection(2000)
    near = ch.Channel(server.nextPendingConnection())
    yield near, far, server
    near.close()
    far.close()
    server.close()


def received(channel) -> list:
    got = []
    channel.message.connect(got.append)
    return got


def test_messages_go_both_ways_in_order(pair):
    near, far, _server = pair
    at_far, at_near = received(far), received(near)

    near.send("start", n=1)
    near.send("pause", n=2)
    far.send("state", state="recording")

    assert wait_for(lambda: len(at_far) == 2 and len(at_near) == 1)
    assert at_far == [{"type": "start", "n": 1}, {"type": "pause", "n": 2}]
    assert at_near == [{"type": "state", "state": "recording"}]


def test_a_message_split_across_reads_arrives_whole(pair):
    near, far, _server = pair
    at_far = received(far)

    near.socket.write(b'{"type": "sta')
    near.socket.flush()
    wait_for(lambda: False, 0.1)
    near.socket.write(b'rt"}\n')
    near.socket.flush()

    assert wait_for(lambda: at_far == [{"type": "start"}])


def test_what_isnt_a_message_is_skipped(pair):
    near, far, _server = pair
    at_far = received(far)

    near.socket.write(b'not json\n[1, 2]\n{"no": "type"}\n{"type": 7}\n\xff\xfe\n\n{"type": "ok"}\n')
    near.socket.flush()

    assert wait_for(lambda: at_far == [{"type": "ok"}]) and far.is_open


def test_a_message_too_long_to_be_real_closes_the_channel(pair):
    near, far, _server = pair
    closed = []
    far.closed.connect(lambda: closed.append(True))

    near.socket.write(b"x" * (ch.MAX_LINE + 100))
    near.socket.flush()

    assert wait_for(lambda: closed == [True]) and not far.is_open
    assert far.send("state") is False


def test_the_other_side_going_away_is_noticed(pair):
    near, far, _server = pair
    closed = []
    far.closed.connect(lambda: closed.append(True))

    near.close()

    assert wait_for(lambda: closed == [True])


def test_only_this_user_can_open_a_recording_channel(qapp):
    server = ch.listen(ch.new_name())
    try:
        assert server.socketOptions() == QLocalServer.SocketOption.UserAccessOption
    finally:
        server.close()


def test_each_recording_gets_a_channel_of_its_own_without_the_user_name_in_it():
    first, second = ch.new_name(), ch.new_name()

    assert first != second and ch.is_name(first) and ch.is_name(second)
    assert os.environ.get("USERNAME", "nobody") not in first  # any local user can list pipe names


def test_messages_sent_before_the_channel_was_made_arrive_once_connected(qapp):
    name = ch.new_name()
    server = ch.listen(name)
    far_socket = ch.connect(name, 2000)
    assert server.waitForNewConnection(2000)
    near_socket = server.nextPendingConnection()
    far_socket.write(b'{"type": "hello"}\n')
    far_socket.flush()
    assert wait_for(lambda: near_socket.bytesAvailable() > 0)

    near = ch.Channel(near_socket)
    got = received(near)  # connected after the channel was made, before Qt's events ran

    assert wait_for(lambda: got == [{"type": "hello"}])
    near.close()
    far_socket.disconnectFromServer()
    server.close()


def test_hostile_json_is_skipped_and_what_follows_still_arrives(pair):
    near, far, _server = pair
    at_far = received(far)

    near.socket.write(b"[" * 60_000 + b"\n" + b"9" * 5_000 + b"\n" + b'{"type": "stop"}\n')
    near.socket.flush()

    assert wait_for(lambda: at_far == [{"type": "stop"}]) and far.is_open


def test_a_long_line_with_its_newline_still_closes_the_channel(pair):
    near, far, _server = pair
    closed = []
    far.closed.connect(lambda: closed.append(True))

    near.socket.write(b"x" * (ch.MAX_LINE + 10) + b"\n")
    near.socket.flush()

    assert wait_for(lambda: closed == [True])


def test_a_last_message_sent_just_before_closing_still_counts(pair):
    near, far, _server = pair
    at_far = received(far)

    near.send("cancel")
    near.close()

    assert wait_for(lambda: not far.is_open)
    assert at_far == [{"type": "cancel"}]


def test_settings_arrive_as_they_were_sent():
    settings = Settings(area=(-1920, 20, 1280, 720), output=Path(OUTPUT), fps=60, max_size=None, microphone=False,
                        microphone_id="{mic}", system_audio=True, speakers_id=None,
                        effects=PointerEffects(pointer=False, highlight=True, clicks=False))

    sent = json.loads(json.dumps(ch.settings_to_message(settings)))

    assert ch.settings_from_message(sent) == settings


@pytest.mark.parametrize("change, reason", [
    ({"output": "rec.mp4"}, "can't be saved"),
    ({"output": OUTPUT[:-4] + ".avi"}, "can't be saved"),
    ({"output": "file://" + OUTPUT}, "can't be saved"),
    ({"output": None}, "can't be saved"),
    ({"microphone": "yes"}, "microphone setting"),
    ({"effects": {"pointer": "no"}}, "pointer setting"),
    ({"microphone_id": 5}, "isn't a device id"),
    ({"speakers_id": "x" * 600}, "isn't a device id"),
    ({"output": "\\\\server\\share\\rec.mp4"}, "can't be saved"),
    ({"output": "\\\\?\\C:\\rec.mp4"}, "can't be saved"),
    ({"output": str(Path(OUTPUT).parent / ".." / "rec.mp4")}, "can't be saved"),
    ({"output": str(Path(OUTPUT).parent / "notes.txt:hidden.mp4")}, "can't be saved"),
    ({"effects": ["pointer"]}, "pointer settings"),
    ({"area": "0,0,320,240"}, "area must be a list"),
    ({"area": [0, 0, 320]}, "four whole numbers"),
    ({"fps": "30"}, "frames a second"),
    ({"max_size": [10, 10]}, "largest size"),
])
def test_settings_that_cant_be_used_are_refused(change, reason):
    good = ch.settings_to_message(Settings(area=(0, 0, 320, 240), output=Path(OUTPUT)))

    with pytest.raises(ValueError, match=reason):
        ch.settings_from_message({**good, **change})


def test_missing_settings_are_refused():
    with pytest.raises(ValueError, match="missing"):
        ch.settings_from_message(None)
