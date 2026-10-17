import datetime as dt

import pytest

from snipforge import share


def test_snip_filename():
    assert share.snip_filename(dt.datetime(2026, 10, 13, 9, 5, 7)) == "Snip 2026-10-13 090507.png"


def test_export_never_overwrites(tmp_path, white_image):
    first = share.export(white_image, tmp_path, "Snip.png")
    second = share.export(white_image, tmp_path, "Snip.png")
    assert first != second and first.exists() and second.exists()


def test_onedrive_accounts_have_real_folders():
    for account in share.onedrive_accounts():
        assert account.folder.is_dir()
        assert account.label


def test_windows_share_verb_is_offered_for_pngs(tmp_path, white_image):
    path = share.export(white_image, tmp_path)
    if not share.has_shell_verb(path, share.WINDOWS_SHARE_VERB):
        pytest.skip("Windows share sheet not available on this machine")
    assert share.has_shell_verb(path, "definitely-not-a-verb") is False


@pytest.mark.windows
def test_shell_menu_lookups_do_not_leak_handles(tmp_path, white_image):
    import ctypes
    path = share.export(white_image, tmp_path)
    process = ctypes.windll.kernel32.GetCurrentProcess()
    gdi = lambda: ctypes.windll.user32.GetGuiResources(process, 0)
    user = lambda: ctypes.windll.user32.GetGuiResources(process, 1)
    share.has_shell_verb(path, share.WINDOWS_SHARE_VERB)  # warm up extension caches
    before = (gdi(), user())
    for _ in range(8):
        share.has_shell_verb(path, share.WINDOWS_SHARE_VERB)
    assert gdi() - before[0] < 20 and user() - before[1] < 20


def test_export_wraps_folder_errors(tmp_path, white_image):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(share.ShareError):
        share.export(white_image, blocker / "sub")


def test_teams_recipients_and_link():
    from snipforge.editor.teams_dialog import chat_link, parse_recipients
    valid, invalid = parse_recipients("a.b@co.com; c@co.com  nope")
    assert valid == ["a.b@co.com", "c@co.com"] and invalid == ["nope"]
    link = chat_link(valid, "Here's the snip")
    assert link.startswith("msteams:/l/chat/0/0?users=a.b%40co.com,c%40co.com")
    assert "message=Here%27s%20the%20snip" in link


def test_version_comparison():
    from snipforge.updates import is_newer, parse_version
    assert parse_version("v1.10.2") == (1, 10, 2)
    assert is_newer("v0.5.0", "0.4.9") and not is_newer("v0.4.0", "0.4.0")
    assert not is_newer("garbage", "0.4.0")


def test_frozen_autostart_command(monkeypatch):
    import sys

    from snipforge import autostart
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\SnipForge\SnipForge.exe")
    assert autostart._command() == r'"C:\Apps\SnipForge\SnipForge.exe" --background'


def test_print_screen_only_without_modifiers(monkeypatch):
    from snipforge import hotkey
    held = set()
    monkeypatch.setattr(hotkey, "_is_down", lambda vk: vk in held)
    assert hotkey.HotkeyListener._matches(hotkey.VK_SNAPSHOT)
    held.add(hotkey.VK_MENU)  # Alt+PrtSc belongs to Windows
    assert not hotkey.HotkeyListener._matches(hotkey.VK_SNAPSHOT)
    held.clear()
    held.update({hotkey.VK_LWIN, hotkey.VK_SHIFT})
    assert hotkey.HotkeyListener._matches(hotkey.VK_S)
    assert not hotkey.HotkeyListener._matches(hotkey.VK_SNAPSHOT)  # Win+PrtSc belongs to Windows


@pytest.mark.windows
def test_all_users_startup_counts_as_enabled(monkeypatch):
    import winreg

    from snipforge import autostart
    monkeypatch.setattr(autostart, "_has_value", lambda hive: hive == winreg.HKEY_LOCAL_MACHINE)
    assert autostart.is_enabled() and autostart.enabled_for_all_users()
