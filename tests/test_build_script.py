"""scripts/build.py helpers: the permanent-download installer copy, checksums and preview versions."""
import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

from snipforge import project

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build = _load("build")


def test_latest_alias_is_an_identical_versionless_copy(tmp_path):
    installer = tmp_path / "SnipForge-0.6.0-setup.exe"
    installer.write_bytes(b"installer bytes")

    alias = build.add_latest_alias(installer)

    assert alias == tmp_path / "SnipForge-setup.exe"
    assert alias.read_bytes() == b"installer bytes"
    assert installer.exists()  # the versioned file stays: the in-app updater looks for it


def test_checksums_list_every_file_including_the_alias(tmp_path):
    files = []
    for name, data in [("SnipForge-0.6.0-portable.zip", b"zip"), ("SnipForge-0.6.0-setup.exe", b"exe"),
                       ("SnipForge-setup.exe", b"exe")]:
        (tmp_path / name).write_bytes(data)
        files.append(tmp_path / name)

    sums = build.write_checksums(files, tmp_path)

    assert sums == tmp_path / "SHA256SUMS.txt"
    assert sums.read_text(encoding="utf-8").splitlines() == [
        f"{hashlib.sha256(b'zip').hexdigest()}  SnipForge-0.6.0-portable.zip",
        f"{hashlib.sha256(b'exe').hexdigest()}  SnipForge-0.6.0-setup.exe",
        f"{hashlib.sha256(b'exe').hexdigest()}  SnipForge-setup.exe",
    ]


def test_checksums_use_unix_line_endings_so_sha256sum_can_check_them(tmp_path):
    (tmp_path / "a.zip").write_bytes(b"zip")
    sums = build.write_checksums([tmp_path / "a.zip"], tmp_path)
    assert b"\r" not in sums.read_bytes()  # `sha256sum -c` on Mac and Linux reads "a.zip\r" as the name


@pytest.mark.parametrize("version, numeric", [
    ("0.5.0", "0.5.0.0"),
    ("0.6.0-preview.1", "0.6.0.1"),
    ("1.2.3-preview.12", "1.2.3.12"),
])
def test_numeric_version_for_windows_file_versions(version, numeric):
    assert build.numeric_version(version) == numeric


def test_stamp_version_rewrites_and_can_be_restored(tmp_path):
    init = tmp_path / "__init__.py"
    original = b'__version__ = "0.5.0"  # x-release-please-version\r\nOTHER = "kept"\r\n'
    init.write_bytes(original)

    saved = build.stamp_version("0.6.0-preview.1", init)

    assert saved == original
    assert init.read_bytes() == original.replace(b"0.5.0", b"0.6.0-preview.1")
    assert build.read_version(init) == "0.6.0-preview.1"


@pytest.mark.parametrize("bad", ["0.6", "v0.6.0", "0.6.0-beta.1", '0.6.0"; import os; "', "0.6.0-preview"])
def test_stamp_version_rejects_unexpected_versions(tmp_path, bad):
    init = tmp_path / "__init__.py"
    init.write_text('__version__ = "0.5.0"\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        build.stamp_version(bad, init)
    assert init.read_text(encoding="utf-8") == '__version__ = "0.5.0"\n'


def test_installer_links_to_the_repository_the_app_names(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(build, "run", commands.append)
    monkeypatch.setattr(build, "find_iscc", lambda: Path("ISCC.exe"))

    build.build_installer(tmp_path / "SnipForge", "0.6.0")

    assert f"/DRepoUrl={project.REPO_URL}" in commands[0]


def test_the_installer_size_is_reported_and_kept_within_its_budget(tmp_path, capsys):
    installer = tmp_path / "SnipForge-1.0.0-setup.exe"
    installer.write_bytes(b"x" * 2_500_000)

    assert build.check_size_budget(installer, budget_mb=3) == pytest.approx(2.5)
    assert "2.5 MB" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="over its 2 MB budget"):
        build.check_size_budget(installer, budget_mb=2)
