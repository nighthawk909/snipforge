"""The snip key's keyboard hook keeps working when SnipForge is busy.

Windows quietly removes a low-level keyboard hook that answers too slowly, and SnipForge's hook can
only answer once no other part of SnipForge holds Python's lock. A key that reaches the hook late
is the sign: the hook is put back straight away instead of at the next periodic refresh.
"""
from snipforge import hotkey


def listener_counting_refreshes(monkeypatch):
    listener = hotkey.HotkeyListener()
    asked = []
    monkeypatch.setattr(listener, "refresh", lambda: asked.append(True))
    return listener, asked


def test_a_key_that_arrives_on_time_changes_nothing(monkeypatch):
    listener, asked = listener_counting_refreshes(monkeypatch)
    listener._check_timing(event_time=10_000, now=10_000 + hotkey.HOOK_LATE_MS - 1)
    assert asked == [] and listener.stalls == 0


def test_a_late_key_puts_the_hook_back_at_once(monkeypatch):
    listener, asked = listener_counting_refreshes(monkeypatch)
    listener._check_timing(event_time=10_000, now=11_200)
    assert asked == [True] and listener.stalls == 1 and listener.late_ms == 1_200


def test_one_stall_asks_once_until_the_hook_is_back(monkeypatch):
    listener, asked = listener_counting_refreshes(monkeypatch)
    for event_time in (10_000, 10_050, 10_100):  # keys held up by the same busy spell
        listener._check_timing(event_time=event_time, now=11_500)
    assert asked == [True] and listener.stalls == 1
    listener._reinstalled()  # the hook thread put it back and reported it
    listener._check_timing(event_time=20_000, now=21_000)  # busy again later
    assert asked == [True, True] and listener.stalls == 2


def test_the_tick_count_wrapping_round_is_not_a_stall(monkeypatch):
    listener, asked = listener_counting_refreshes(monkeypatch)
    listener._check_timing(event_time=0xFFFF_FFF0, now=0x10)  # 32 ms, across the wrap after ~49 days
    assert asked == []
    listener._check_timing(event_time=0xFFFF_FF00, now=0x200)  # 768 ms across the wrap: late
    assert asked == [True] and listener.late_ms == 0x300


def test_an_event_stamped_after_now_is_not_a_stall(monkeypatch):
    listener, asked = listener_counting_refreshes(monkeypatch)
    listener._check_timing(event_time=10_005, now=10_000)  # clocks read a moment apart
    assert asked == []


def test_the_app_checks_on_the_hook_often(monkeypatch):
    from snipforge import app
    assert app.REHOOK_MS <= 60_000  # a hook Windows removed comes back within a minute at worst


def test_the_log_says_so_at_most_once_a_minute(monkeypatch, caplog):
    import logging
    listener, _asked = listener_counting_refreshes(monkeypatch)
    caplog.set_level(logging.INFO, logger="snipforge.hotkey")
    for _ in range(3):  # three busy spells in a row
        listener._check_timing(event_time=10_000, now=11_000)
        listener._reinstalled(True)
    assert len([r for r in caplog.records if "busy" in r.getMessage()]) == 1
    assert listener.stalls == 3 and listener.late_ms == 0


def test_a_failed_reinstall_is_not_reported_as_put_back(monkeypatch, caplog):
    import logging
    listener, _asked = listener_counting_refreshes(monkeypatch)
    caplog.set_level(logging.INFO, logger="snipforge.hotkey")
    listener._check_timing(event_time=10_000, now=11_000)
    listener._reinstalled(False)
    assert not [r for r in caplog.records if "put back" in r.getMessage()]
