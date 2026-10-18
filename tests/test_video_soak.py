"""scripts/soak_recording.py: the soak test's made-up screen and sound cards, and its verdicts. A short
end-to-end soak runs with the stress tests (python -m pytest -m stress)."""
import importlib.util
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("soak_recording", ROOT / "scripts" / "soak_recording.py")
soak = importlib.util.module_from_spec(spec)
spec.loader.exec_module(soak)


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
    step = 1 / 48000
    first = soak.beep_samples(2.05, 480, step, "system")  # 50 ms into the 100 ms beep
    after = soak.beep_samples(2.0 + soak.BEEP, 480, step, "system")

    assert first is not None and all(abs(v) <= soak.LOUD for v in first) and any(v != 0 for v in first)
    assert after is None  # it's over


def test_memory_that_stays_level_passes_and_growth_fails():
    level = [{"memory_mb": 700 + n % 3} for n in range(20)]
    growing = [{"memory_mb": 700 + 10 * n} for n in range(20)]

    assert soak.memory_verdict(level)["ok"] is True
    verdict = soak.memory_verdict(growing)
    assert verdict["ok"] is False and verdict["growth_mb"] == 170.0
    assert soak.memory_verdict(level[:2])["growth_mb"] is None  # too short to tell


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
