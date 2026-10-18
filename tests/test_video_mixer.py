"""snipforge/video/clock.py and mixer.py: microphone and system audio placed on one timeline by their
timestamps, with pauses, gaps, jitter, bad timestamps, late packets and clock drift. No devices
needed: packets are made up."""
import array
import random
import threading

import pytest

from snipforge.video.clock import Clock
from snipforge.video.mixer import HOLD_BACK, Mixer

RATE = 48000
START = 100.0  # the wall-clock second recording starts at
NOW = 1e9  # "now", for the mixer's never-from-the-future check: long after every test stamp


def packet(value: float, frames: int = 480) -> bytes:
    return array.array("f", [value] * (frames * 2)).tobytes()


def ramp(first: int, frames: int) -> bytes:
    """Each sample's value says where it was made, so misplacement shows."""
    return array.array("f", [((first + i) % 1000) / 1000 for i in range(frames) for _ in (0, 1)]).tobytes()


def make(*sources, gain=None, rate=RATE):
    clock = Clock()
    clock.begin(START)
    mixer = Mixer(clock, rate, now=lambda: NOW)
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


def test_beginning_again_forgets_earlier_pauses():
    clock = Clock()
    clock.begin(0.0)
    clock.pause(1.0)
    clock.resume(2.0)
    clock.begin(10.0)
    assert clock.media_time(11.0) == pytest.approx(1.0) and clock.paused_total == 0


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


def test_slightly_jittery_timestamps_make_one_unbroken_stream():
    _clock, mixer = make("mic")
    rng = random.Random(7)
    for i in range(100):
        stamp = START + i * 0.01 + (rng.uniform(-0.0005, 0.0005) if i else 0)  # the first one anchors the rest
        mixer.push("mic", ramp(i * 480, 480), 480, stamp, False)

    values = left(mixer.pull(1.0)[1])

    assert values == pytest.approx([(i % 1000) / 1000 for i in range(48000)])


def test_rougher_jitter_is_followed_without_jumps_or_drifting_off():
    _clock, mixer = make("mic")
    rng = random.Random(3)
    for i in range(500):  # 5 s, every stamp off by up to 3 ms
        mixer.push("mic", packet(0.1), 480, START + i * 0.01 + rng.uniform(-0.003, 0.003), False)

    source = mixer.sources["mic"]
    assert source.resyncs == 0 and source.bad_stamps == 0
    assert abs(source.next_index - 5 * RATE) <= RATE * 0.003


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


def test_even_a_short_gap_puts_the_next_sound_on_time():
    _clock, mixer = make("system")
    mixer.push("system", packet(0.25), 480, START, False)
    mixer.push("system", packet(0.75), 480, START + 0.03, False)  # 20 ms of nothing in between

    values = left(mixer.pull(0.04)[1])

    assert set(values[480:1440]) == {0.0}
    assert set(values[1440:1920]) == {0.75}


def test_two_sources_add_up_and_never_clip_past_full_scale():
    _clock, mixer = make("mic", "system")
    mixer.push("mic", packet(0.25), 480, START, False)
    mixer.push("system", packet(0.5), 480, START, False)
    mixer.push("mic", packet(0.75), 480, START + 0.01, False)
    mixer.push("system", packet(0.75), 480, START + 0.01, False)

    values = left(mixer.pull(0.02)[1])

    assert set(values[:480]) == {0.75}
    assert set(values[480:960]) == {1.0}


def test_gain_is_applied_to_every_source():
    _clock, mixer = make("mic", "system", gain={"mic": 0.5, "system": 0.25})
    mixer.push("mic", packet(0.5), 480, START, False)
    mixer.push("system", packet(0.8), 480, START, False)

    assert left(mixer.pull(0.01)[1]) == pytest.approx([0.45] * 480)


def test_other_sample_rates_work_the_same():
    _clock, mixer = make("mic", rate=44100)
    mixer.push("mic", packet(0.5, 441), 441, START + 0.5, False)

    values = left(mixer.pull(1.0)[1])

    assert len(values) == 44100 and set(values[22050:22491]) == {0.5}


def test_packets_before_the_start_are_dropped_and_one_across_it_is_cut():
    _clock, mixer = make("mic")
    mixer.push("mic", packet(0.5), 480, START - 0.015, False)
    mixer.push("mic", ramp(0, 480), 480, START - 0.005, False)  # follows on; 240 of its frames are before the start

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


# --- timestamps that can't be right ------------------------------------------------------------

def test_a_stamp_from_the_future_is_ignored_and_the_source_keeps_playing():
    """One bad stamp must never silence a source for the rest of the recording."""
    _clock, mixer = make("mic")
    mixer.push("mic", packet(0.25), 480, START, False)
    mixer.push("mic", packet(0.5), 480, NOW + 60, False)  # "captured" a minute from now
    for i in range(2, 50):
        mixer.push("mic", packet(0.75), 480, START + i * 0.01, False)

    values = left(mixer.pull(0.5)[1])

    assert set(values[:480]) == {0.25}
    assert set(values[480:960]) == {0.5}  # followed on from the last good one
    assert set(values[960:24000]) == {0.75}
    assert mixer.sources["mic"].bad_stamps == 1


def test_a_stamp_windows_calls_unreliable_follows_on():
    _clock, mixer = make("mic")
    mixer.push("mic", packet(0.25), 480, START, False)
    mixer.push("mic", packet(0.5), 480, None, False)

    values = left(mixer.pull(0.02)[1])

    assert set(values[:480]) == {0.25} and set(values[480:960]) == {0.5}


def test_a_zero_stamp_is_followed_on_not_trusted():
    _clock, mixer = make("mic")
    mixer.push("mic", packet(0.25), 480, START, False)
    mixer.push("mic", packet(0.5), 480, 0.0, False)  # QPC 0: a hundred seconds before the start

    values = left(mixer.pull(0.02)[1])

    assert set(values[480:960]) == {0.5}
    assert mixer.sources["mic"].bad_stamps == 1


def test_a_jump_backwards_is_taken_as_a_bad_stamp():
    _clock, mixer = make("mic")
    mixer.push("mic", packet(0.25, 4800), 4800, START, False)
    mixer.push("mic", packet(0.5, 4800), 4800, START + 0.03, False)  # 70 ms early

    values = left(mixer.pull(0.25)[1])

    assert set(values[:4800]) == {0.25}
    assert set(values[4800:9600]) == {0.5}  # straight after, not on top
    assert set(values[9600:]) == {0.0}


def test_a_first_packet_without_a_stamp_has_nowhere_to_go():
    _clock, mixer = make("mic")
    mixer.push("mic", packet(0.5), 480, None, False)

    assert mixer.sources["mic"].samples == 0


# --- late packets --------------------------------------------------------------------------------

def test_sound_that_comes_after_its_time_was_handed_out_is_counted():
    _clock, mixer = make("mic")
    mixer.pull(0.1)
    mixer.push("mic", packet(0.5), 480, START + 0.095, False)  # half of it is late

    assert mixer.sources["mic"].late_frames == 240
    assert set(left(mixer.pull(0.2)[1])[:240]) == {0.5}


def test_pull_ready_keeps_back_time_for_packets_still_on_their_way():
    _clock, mixer = make("mic")
    mixer.push("mic", packet(0.5, 4800), 4800, START, False)

    first, samples = mixer.pull_ready(START + 1.0)

    assert first == 0 and len(samples) == round((1.0 - HOLD_BACK) * RATE) * 2
    mixer.push("mic", packet(0.25), 480, START + 0.8, False)  # arrives 200 ms after its stamp: still in time
    assert mixer.sources["mic"].late_frames == 0


def test_a_source_can_start_after_mixing_has_begun():
    _clock, mixer = make("mic", "system")
    mixer.push("mic", packet(0.25, 48000), 48000, START, False)
    mixer.pull(0.5)
    mixer.push("system", packet(0.5), 480, START + 0.7, False)

    values = left(mixer.pull(1.0)[1])

    assert set(values[9600:10080]) == {0.75}  # 0.7 s, mixed with the microphone


# --- pauses -------------------------------------------------------------------------------------

def test_sound_during_a_pause_is_left_out_and_the_timeline_closes_up():
    clock, mixer = make("mic")
    for i in range(100):  # sound right up to the pause
        mixer.push("mic", packet(0.25), 480, START + i * 0.01, False)
    clock.pause(START + 1.0)
    mixer.push("mic", packet(0.5), 480, START + 1.5, False)  # while paused
    clock.resume(START + 3.0)
    for i in range(50):  # and straight after it
        mixer.push("mic", packet(0.75), 480, START + 3.0 + i * 0.01, False)

    values = left(mixer.pull(1.5)[1])

    assert 0.5 not in values
    assert set(values[:48000]) == {0.25}
    assert set(values[48000:72000]) == {0.75}  # carries on at 1.0 s


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
    assert source.resyncs == 0 and source.bad_stamps == 0  # corrected smoothly, never by jumping
    assert abs(source.adjustments) == pytest.approx(abs(device_rate - RATE) * 60, rel=0.1)
    assert abs(source.next_index - 60 * RATE) <= RATE * 0.003  # within 3 ms after a minute


def test_an_added_sample_is_between_its_neighbours():
    _clock, mixer = make("mic")
    mixer.push("mic", ramp(0, 480), 480, START, False)
    mixer.push("mic", ramp(480, 480), 480, START + 0.013, False)  # 3 ms late: the card runs slow

    values = left(mixer.pull(0.03)[1])

    assert mixer.sources["mic"].adjustments == 1
    assert values[720] == pytest.approx((values[719] + values[721]) / 2)


def test_a_glitch_windows_reports_jumps_to_the_timestamp_at_once():
    _clock, mixer = make("mic")
    mixer.push("mic", packet(0.25), 480, START, False)
    mixer.push("mic", packet(0.5), 480, START + 0.014, True)  # only 4 ms off, but flagged

    values = left(mixer.pull(0.03)[1])

    assert set(values[480:672]) == {0.0}
    assert set(values[672:1152]) == {0.5}
    assert mixer.sources["mic"].resyncs == 1


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
    for name in ("mic", "system"):  # every frame either placed or counted as late, never lost unseen
        source = mixer.sources[name]
        assert source.samples + source.late_frames == 200 * 480
