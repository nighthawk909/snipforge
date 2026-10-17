"""snipforge/video/clock.py and mixer.py: microphone and system audio placed on one timeline by their
timestamps, with pauses, gaps, jitter and clock drift. No devices needed: packets are made up."""
import array
import random
import threading

import pytest

from snipforge.video.clock import Clock
from snipforge.video.mixer import Mixer

RATE = 48000
START = 100.0  # the wall-clock second recording starts at


def packet(value: float, frames: int = 480) -> bytes:
    return array.array("f", [value] * (frames * 2)).tobytes()


def ramp(first: int, frames: int) -> bytes:
    """Each sample's value says where it was made, so misplacement shows."""
    return array.array("f", [((first + i) % 1000) / 1000 for i in range(frames) for _ in (0, 1)]).tobytes()


def make(*sources, gain=None):
    clock = Clock()
    clock.begin(START)
    mixer = Mixer(clock, RATE)
    for name in sources:
        mixer.add_source(name, gain=(gain or {}).get(name, 1.0))
    return clock, mixer


def left(samples) -> list[float]:
    return list(samples[0::2])


# --- the clock ----------------------------------------------------------------------------------

def test_the_clock_counts_from_the_start_and_leaves_out_pauses():
    clock = Clock()
    assert clock.media_time(5.0) is None  # not started
    clock.begin(10.0)
    assert clock.media_time(9.0) == pytest.approx(-1.0)  # before the start: negative
    assert clock.media_time(12.0) == pytest.approx(2.0)
    clock.pause(13.0)
    assert clock.paused and clock.media_time(14.0) is None
    clock.resume(16.0)
    assert not clock.paused
    assert clock.media_time(17.0) == pytest.approx(4.0)  # 7 s since the start, 3 s of it paused
    assert clock.media_time(13.5) is None  # inside the pause
    assert clock.paused_total == pytest.approx(3.0)


def test_pausing_twice_or_resuming_unpaused_changes_nothing():
    clock = Clock()
    clock.begin(0.0)
    clock.resume(1.0)
    clock.pause(2.0)
    clock.pause(3.0)
    clock.resume(4.0)
    assert clock.media_time(5.0) == pytest.approx(3.0)


# --- placing packets ----------------------------------------------------------------------------

def test_a_packet_lands_where_its_timestamp_says():
    _clock, mixer = make("mic")
    mixer.push("mic", packet(0.5), 480, START + 0.5, False)

    first, samples = mixer.pull(1.0)

    assert first == 0 and len(samples) == RATE * 2
    values = left(samples)
    assert set(values[:24000]) == {0.0}
    assert set(values[24000:24480]) == {0.5}
    assert set(values[24480:]) == {0.0}


def test_jittery_timestamps_still_make_one_unbroken_stream():
    _clock, mixer = make("mic")
    rng = random.Random(7)
    for i in range(100):
        stamp = START + i * 0.01 + (rng.uniform(-0.0005, 0.0005) if i else 0)  # the first one anchors the rest
        mixer.push("mic", ramp(i * 480, 480), 480, stamp, False)

    values = left(mixer.pull(1.0)[1])

    assert values == pytest.approx([(i % 1000) / 1000 for i in range(48000)])


def test_a_silent_stretch_becomes_silence_and_sound_resumes_on_time():
    """Windows sends no system-audio packets at all while nothing is playing."""
    _clock, mixer = make("system")
    for i in range(10):
        mixer.push("system", packet(0.25), 480, START + i * 0.01, False)
    mixer.push("system", packet(0.75), 480, START + 0.5, False)

    values = left(mixer.pull(0.6)[1])

    assert set(values[:4800]) == {0.25}
    assert set(values[4800:24000]) == {0.0}
    assert set(values[24000:24480]) == {0.75}


def test_two_sources_add_up_and_never_clip_past_full_scale():
    _clock, mixer = make("mic", "system")
    mixer.push("mic", packet(0.25), 480, START, False)
    mixer.push("system", packet(0.5), 480, START, False)
    mixer.push("mic", packet(0.75), 480, START + 0.01, False)
    mixer.push("system", packet(0.75), 480, START + 0.01, False)

    values = left(mixer.pull(0.02)[1])

    assert set(values[:480]) == {0.75}
    assert set(values[480:960]) == {1.0}


def test_gain_is_applied_per_source():
    _clock, mixer = make("mic", gain={"mic": 0.5})
    mixer.push("mic", packet(0.5), 480, START, False)

    assert set(left(mixer.pull(0.01)[1])) == {0.25}


def test_packets_before_the_start_are_dropped_and_one_across_it_is_cut():
    _clock, mixer = make("mic")
    mixer.push("mic", packet(0.5), 480, START - 0.015, False)
    mixer.push("mic", ramp(0, 480), 480, START - 0.005, False)  # follows on; 240 of its frames come before the start

    values = left(mixer.pull(0.01)[1])

    assert values[:240] == pytest.approx([(240 + i) / 1000 for i in range(240)])


def test_nothing_is_handed_out_twice_and_blocks_follow_on():
    _clock, mixer = make("mic")
    mixer.push("mic", packet(0.5, 4800), 4800, START, False)

    first_a, a = mixer.pull(0.05)
    first_b, b = mixer.pull(0.1)

    assert (first_a, len(a)) == (0, 2400 * 2)
    assert (first_b, len(b)) == (2400, 2400 * 2)
    assert mixer.pull(0.1) is None
    assert mixer.pull(0.05) is None


# --- pauses -------------------------------------------------------------------------------------

def test_sound_during_a_pause_is_left_out_and_the_timeline_closes_up():
    clock, mixer = make("mic")
    clock.pause(START + 1.0)
    mixer.push("mic", packet(0.5), 480, START + 1.5, False)  # while paused
    clock.resume(START + 3.0)
    mixer.push("mic", packet(0.75), 480, START + 3.5, False)  # 2 s of pause later: lands at 1.5 s

    values = left(mixer.pull(2.0)[1])

    assert 0.5 not in values
    assert set(values[72000:72480]) == {0.75}


# --- clock drift and jumps ----------------------------------------------------------------------

@pytest.mark.parametrize("device_rate", [48048, 47952])  # a sound card 0.1% fast, and one 0.1% slow
def test_a_drifting_sound_card_stays_in_place_without_jumps(device_rate):
    """The card makes `device_rate` samples per real second but says 48 kHz; its timestamps are the
    true times. A minute later its sound must still be where the timestamps put it."""
    _clock, mixer = make("mic")
    frames = 480
    for i in range(device_rate * 60 // frames):
        mixer.push("mic", packet(0.5, frames), frames, START + i * frames / device_rate, False)

    source = mixer.sources["mic"]
    assert source.resyncs == 0  # corrected smoothly, never by jumping
    assert abs(source.adjustments) == pytest.approx(abs(device_rate - RATE) * 60, rel=0.1)
    true_end = 60 * RATE
    assert abs(source.next_index - true_end) <= RATE * 0.003  # within 3 ms after a minute


def test_a_big_jump_or_a_glitch_resyncs_at_once():
    _clock, mixer = make("mic")
    mixer.push("mic", packet(0.25), 480, START, False)
    mixer.push("mic", packet(0.5), 480, START + 0.2, True)  # Windows flagged a discontinuity

    values = left(mixer.pull(0.3)[1])

    assert set(values[9600:10080]) == {0.5}
    assert mixer.sources["mic"].resyncs == 1


def test_a_packet_that_overlaps_the_last_one_is_not_heard_twice():
    _clock, mixer = make("mic")
    mixer.push("mic", packet(0.25, 4800), 4800, START, False)
    mixer.push("mic", packet(0.5, 4800), 4800, START + 0.03, False)  # 70 ms early: a resync backwards

    values = left(mixer.pull(0.2)[1])

    assert max(values) <= 0.5  # never 0.75: the overlap isn't added on top


# --- meters and threads -------------------------------------------------------------------------

def test_levels_report_the_peak_since_they_were_last_read():
    _clock, mixer = make("mic", "system")
    mixer.push("mic", packet(-0.8), 480, START, False)
    mixer.push("system", packet(0.3), 480, START, False)

    assert mixer.levels() == pytest.approx({"mic": 0.8, "system": 0.3})
    assert mixer.levels() == {"mic": 0.0, "system": 0.0}
    assert mixer.sources["mic"].max_peak == pytest.approx(0.8)


def test_packets_can_arrive_from_several_threads_while_sound_is_taken_out():
    _clock, mixer = make("mic", "system")

    def feed(name):
        for i in range(200):
            mixer.push(name, packet(0.1), 480, START + i * 0.01, False)

    threads = [threading.Thread(target=feed, args=(name,)) for name in ("mic", "system")]
    for t in threads:
        t.start()
    taken = []
    while any(t.is_alive() for t in threads):
        block = mixer.pull(0.5)
        if block:
            taken.append(block)
    for t in threads:
        t.join()
    block = mixer.pull(2.0)
    if block:
        taken.append(block)

    assert sum(len(b[1]) for b in taken) == 2 * RATE * 2
    assert mixer.sources["mic"].samples == mixer.sources["system"].samples == 200 * 480
