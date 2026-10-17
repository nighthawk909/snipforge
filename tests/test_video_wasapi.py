"""snipforge/video/wasapi.py without sound devices: messages and the structures passed to Windows.
The desktop suite (tests/desktop/test_video_audio.py) records from real devices."""
import ctypes

from snipforge.video import wasapi


def test_a_blocked_microphone_says_where_to_turn_it_on():
    error = wasapi.explain(0x80070005, wasapi.MICROPHONE)

    assert isinstance(error, wasapi.MicrophoneBlocked)
    assert "Let desktop apps access your microphone" in str(error)


def test_other_failures_are_explained_in_words():
    assert "unplugged" in str(wasapi.explain(0x88890004, wasapi.SYSTEM))
    assert "Another app" in str(wasapi.explain(0x8889000A, wasapi.MICROPHONE))
    assert "0x80004005" in str(wasapi.explain(0x80004005, wasapi.SYSTEM))
    assert not isinstance(wasapi.explain(0x80070005, wasapi.SYSTEM), wasapi.MicrophoneBlocked)


def test_the_structures_windows_reads_have_its_sizes():
    assert ctypes.sizeof(wasapi.WAVEFORMATEX) == 18
    assert ctypes.sizeof(wasapi.PROPERTYKEY) == 20
    assert ctypes.sizeof(wasapi.PROPVARIANT) == 24
