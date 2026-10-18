"""scripts/soak_recording.py: the soak test's made-up screen and sound cards, and its verdicts. A short
end-to-end soak runs with the stress tests (python -m pytest -m stress)."""
import importlib.util
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


soak = _load("soak_recording")
STEP = 1 / 48000


def test_flashes_come_every_few_seconds_with_room_after_the_last():
    assert soak.mark_times(13.0) == [2.0, 7.0, 12.0]
    assert soak.mark_times(12.5) == [2.0, 7.0]  # 12 s would end too near the end to be seen
    assert soak.mark_times(1.0) == []


def test_the_two_sound_cards_take_turns_to_beep():
    assert [soak.beeper(n) for n in range(4)] == ["system", "microphone", "system", "microphone"]


@pytest.mark.parametrize("media, lit", [(1.99, False), (2.0, True), (2.19, True), (2.2, False), (7.05, True),
                                        (6.99, False)])
def test_the_box_flashes_at_each_mark(media, lit):
    assert soak.flashing(media) is lit


def test_a_beep_starts_on_the_sample_its_mark_falls_on():
    step = 1 / 48000
    data = soak.beep_samples(2.0 - 100 * step, 480, step, "system")  # the mark is 100 samples in

    assert data is not None and len(data) == 960
    assert all(v == 0.0 for v in data[:200])  # silence before it, both channels
    assert data[200] == pytest.approx(0.0, abs=1e-6) and data[201] == data[200]  # sin(0): it starts here...
    assert data[202] == pytest.approx(soak.LOUD * math.sin(2 * math.pi * soak.BEEP_HZ * step), rel=1e-5)
    assert data[202] == data[203]  # ...in both channels


def test_a_beep_is_only_the_turn_taker_s_and_only_near_its_mark():
    step = 1 / 48000
    assert soak.beep_samples(2.0, 480, step, "microphone") is None  # the system card's turn
    assert soak.beep_samples(3.0, 480, step, "system") is None  # nothing due
    assert soak.beep_samples(7.0, 480, step, "microphone") is not None


def test_a_beep_running_past_a_packet_carries_on_in_the_next():
    first = soak.beep_samples(2.0 - 100 * STEP, 480, STEP, "system")  # starts 100 samples in
    following = soak.beep_samples(2.0 + 380 * STEP, 480, STEP, "system")  # the next packet

    expected = soak.LOUD * math.sin(2 * math.pi * soak.BEEP_HZ * 380 * STEP)
    assert first[2 * 479] == pytest.approx(soak.LOUD * math.sin(2 * math.pi * soak.BEEP_HZ * 379 * STEP), abs=1e-4)
    assert following[0] == pytest.approx(expected, abs=1e-4)  # on from where the last left off
    lit = sum(1 for packet in (soak.beep_samples(2.0 - 100 * STEP + n * 480 * STEP, 480, STEP, "system")
                               for n in range(12)) if packet for v in packet[::2] if v != 0)
    assert abs(lit - soak.BEEP * 48000) <= 2  # 100 ms of it in all, none twice
    assert soak.beep_samples(2.0 + soak.BEEP, 480, STEP, "system") is None  # it's over


def test_a_packet_ending_just_as_a_beep_starts_is_silence():
    assert soak.beep_samples(2.0 - 480 * STEP, 480, STEP, "system") is None


def test_memory_that_stays_level_passes_and_growth_fails():
    level = [{"wall_s": 30.0 * n, "memory_mb": 700 + n % 3} for n in range(20)]
    growing = [{"wall_s": 30.0 * n, "memory_mb": 700 + 10 * n} for n in range(20)]

    assert soak.memory_verdict(level, 600)["ok"] is True
    verdict = soak.memory_verdict(growing, 600)
    assert verdict["ok"] is False and verdict["growth_mb"] == 170.0
    assert soak.memory_verdict(level[:2], 60)["growth_mb"] is None  # too short to tell


@pytest.mark.stress
def test_a_short_soak_keeps_sound_with_picture(tmp_path, qapp):
    """Twelve seconds of the soak: both made-up sound cards' beeps on their flashes, though one's
    clock runs 0.1% fast and the other's 0.1% slow."""
    import json

    code = soak.main(["--seconds", "12", "--size", "320x240", "--out", str(tmp_path), "--normal-priority"])

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert code == 0 and report["ok"], report
    assert {row["source"] for row in report["timing"]["rows"]} == {"system", "microphone"}
    assert report["timing"]["worst"] <= soak.TOLERANCE


@pytest.mark.stress
def test_the_soak_notices_sound_out_of_step_with_the_picture(tmp_path, qapp, monkeypatch):
    import json

    on_time = soak.flashing
    monkeypatch.setattr(soak, "flashing", lambda media: on_time(media - 0.2))  # flashes 0.2 s late

    code = soak.main(["--seconds", "9", "--size", "320x240", "--out", str(tmp_path), "--normal-priority"])

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert code == 1 and report["timing"]["ok"] is False
    assert report["timing"]["worst"] == pytest.approx(0.2, abs=0.04)


def sound(adjust_system=-48_000, adjust_microphone=48_000, **troubles):
    """The recorder's sound stats after 1000 s with the made-up cards 0.1% off."""
    return {kind: {"placed_s": 1000.0, "adjustments": adjusted, "resyncs": 0, "bad_stamps": 0, "late_frames": 0,
                   **troubles.get(kind, {})}
            for kind, adjusted in (("system", adjust_system), ("microphone", adjust_microphone))}


def test_the_mixer_following_both_cards_gently_passes():
    verdict = soak.sound_verdict(sound(), 0.001)

    assert verdict["ok"] and verdict["sources"]["system"]["expected"] == -48_000


@pytest.mark.parametrize("stats", [
    sound(adjust_system=0),  # the fast card's drift never corrected
    sound(adjust_microphone=-48_000),  # corrected the wrong way
    sound(system={"resyncs": 6}),  # kept in time by jumping, not by adding a sample now and then
    sound(microphone={"late_frames": 3}),
    {"system": sound()["system"]},  # a card missing altogether
])
def test_the_mixer_jumping_or_not_following_fails(stats):
    assert soak.sound_verdict(stats, 0.001)["ok"] is False


def test_memory_reports_its_slope_and_a_long_run_without_readings_fails():
    creeping = [{"wall_s": 60.0 * n, "memory_mb": 700 + 0.5 * n} for n in range(30)]

    verdict = soak.memory_verdict(creeping, 1800)
    assert verdict["ok"] and verdict["slope_mb_per_min"] == pytest.approx(0.5)
    assert soak.memory_verdict([{"wall_s": 1.0, "memory_mb": None}] * 10, 3600)["ok"] is False
    assert soak.memory_verdict([], 30)["ok"] is True  # a short run: nothing to tell


def test_timing_fails_when_a_beep_or_flash_is_missing(monkeypatch):
    monkeypatch.setattr(soak, "flash_onsets", lambda path, marks: {m: m for m in marks})
    monkeypatch.setattr(soak, "beep_onsets", lambda path, marks: ({m: (None if m == 7.0 else m) for m in marks}, None))

    result = soak.timing("x.mp4", 20.0)

    assert result["ok"] is False and result["missing"] == 1 and result["worst"] == 0.0
    monkeypatch.setattr(soak, "flash_onsets", lambda path, marks: {})
    assert soak.timing("x.mp4", 1.0)["ok"] is False  # no marks at all: nothing shown


def test_memory_can_be_read_here():
    memory = soak.memory_mb()

    assert memory is not None and memory > 1
