"""scripts/port_repo.py and docs/PORTING.md: moving SnipForge to another repository (for example a
copy on GitHub Enterprise) is one command, and nothing is left pointing at the old home."""
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from snipforge import project

ROOT = Path(__file__).resolve().parents[1]
NEW_REPO = "acme-test/snipforge-copy"
GHE = "https://ghe.example.com"
GHE_PAGES = "https://pages.ghe.example.com/acme-test/snipforge-copy/"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


port = _load("port_repo")


def copy_repo(tmp_path) -> Path:
    """The files a port can change: those naming the repository, and snipforge/project.py."""
    root = tmp_path / "copy"
    for path in port.candidate_files(ROOT):
        rel = path.relative_to(ROOT)
        if rel != port.LOCATION_FILE and not names_the_repository(path.read_text(encoding="utf-8")):
            continue
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    return root


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def old_pages_host() -> str | None:
    if project.SERVER_URL != "https://github.com":
        return None
    return f"{project.REPO.split('/')[0].lower()}.github.io"


def names_the_repository(text: str) -> bool:
    pages = old_pages_host()
    return project.REPO in text or bool(pages and pages in text.lower())


def test_port_to_an_enterprise_server_leaves_nothing_pointing_at_the_old_home(tmp_path, capsys):
    root = copy_repo(tmp_path)

    assert port.main(["--root", str(root), "--repo", NEW_REPO, "--server-url", GHE, "--pages-url", GHE_PAGES]) == 0

    for path in port.candidate_files(root):
        rel = path.relative_to(root).as_posix()
        if rel not in port.KEEP:
            assert not names_the_repository(path.read_text(encoding="utf-8")), rel
    location = (root / "snipforge" / "project.py").read_text(encoding="utf-8")
    assert f'SERVER_URL = "{GHE}"\n' in location and f'REPO = "{NEW_REPO}"\n' in location
    assert f"ghe.example.com/api/v3/repos/{NEW_REPO}/releases" in (root / "docs" / "IT-REVIEW.md").read_text(
        encoding="utf-8")
    assert f"{GHE}/{NEW_REPO}/releases/latest/download/SnipForge-setup.exe" in (root / "README.md").read_text(
        encoding="utf-8")
    assert "README.md" in capsys.readouterr().out


def test_port_within_the_same_server_only_changes_the_repository(tmp_path):
    root = copy_repo(tmp_path)

    assert port.main(["--root", str(root), "--repo", NEW_REPO]) == 0

    location = (root / "snipforge" / "project.py").read_text(encoding="utf-8")
    assert f'SERVER_URL = "{project.SERVER_URL}"\n' in location and f'REPO = "{NEW_REPO}"\n' in location
    assert f"{project.REPO_URL.replace(project.REPO, NEW_REPO)}/issues" in (root / "README.md").read_text(
        encoding="utf-8")


def test_port_twice_changes_nothing_more_even_when_the_new_name_extends_the_old(tmp_path):
    root = copy_repo(tmp_path)
    longer = f"{project.REPO}-work"
    args = ["--root", str(root), "--repo", longer, "--pages-url", GHE_PAGES]

    assert port.main(args) == 0
    first = snapshot(root)
    assert port.main(args) == 0

    assert snapshot(root) == first
    assert f"{longer}-work" not in (root / "README.md").read_text(encoding="utf-8")


def test_port_from_rewrites_references_merged_in_later(tmp_path):
    """After a port, project.py names the new home; --from names the old one again, for changes
    merged in from the public repository later."""
    root = copy_repo(tmp_path)
    assert port.main(["--root", str(root), "--repo", NEW_REPO, "--server-url", GHE]) == 0
    (root / "docs" / "LATER.md").write_text(f"Report it at {project.REPO_URL}/issues.\n", encoding="utf-8")

    assert port.main(["--root", str(root), "--repo", NEW_REPO, "--from", project.REPO_URL]) == 0

    assert (root / "docs" / "LATER.md").read_text(encoding="utf-8") == f"Report it at {GHE}/{NEW_REPO}/issues.\n"
    assert f'SERVER_URL = "{GHE}"\n' in (root / "snipforge" / "project.py").read_text(encoding="utf-8")


def test_from_with_a_name_that_extends_the_old_one_does_not_double_it(tmp_path):
    root = copy_repo(tmp_path)
    longer = f"{project.REPO}-corp"
    assert port.main(["--root", str(root), "--repo", longer]) == 0

    assert port.main(["--root", str(root), "--repo", longer, "--from", project.REPO_URL]) == 0

    readme = (root / "README.md").read_text(encoding="utf-8")
    assert f"{longer}/" in readme and f"{longer}-corp" not in readme


def test_from_accepts_a_clone_address(tmp_path):
    root = copy_repo(tmp_path)
    assert port.main(["--root", str(root), "--repo", NEW_REPO, "--server-url", GHE]) == 0
    (root / "docs" / "LATER.md").write_text(f"See {project.REPO_URL}/issues.\n", encoding="utf-8")

    assert port.main(["--root", str(root), "--repo", NEW_REPO, "--from", f"{project.REPO_URL}.git"]) == 0

    assert (root / "docs" / "LATER.md").read_text(encoding="utf-8") == f"See {GHE}/{NEW_REPO}/issues.\n"


def test_port_leaves_other_repositories_with_a_longer_name_alone(tmp_path):
    root = copy_repo(tmp_path)
    sibling = f"{project.REPO_URL}-extras and {project.REPO}-extras and {project.REPO}_old\n"
    (root / "docs" / "SIBLING.md").write_text(sibling, encoding="utf-8")

    assert port.main(["--root", str(root), "--repo", NEW_REPO]) == 0

    assert (root / "docs" / "SIBLING.md").read_text(encoding="utf-8") == sibling


def test_port_keeps_each_files_line_endings(tmp_path):
    root = copy_repo(tmp_path)
    readme = root / "README.md"
    crlf = readme.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    readme.write_bytes(crlf)
    lf = (root / "SECURITY.md").read_bytes().replace(b"\r\n", b"\n")
    (root / "SECURITY.md").write_bytes(lf)

    assert port.main(["--root", str(root), "--repo", NEW_REPO]) == 0

    ported = readme.read_bytes()
    assert NEW_REPO.encode() in ported and ported.count(b"\n") == ported.count(b"\r\n") == crlf.count(b"\r\n")
    assert b"\r\n" not in (root / "SECURITY.md").read_bytes()


def test_port_leaves_the_changelog_and_this_guide_alone(tmp_path):
    root = copy_repo(tmp_path)
    kept = {rel: (root / rel).read_bytes() for rel in port.KEEP}

    assert port.main(["--root", str(root), "--repo", NEW_REPO, "--server-url", GHE]) == 0

    assert {rel: (root / rel).read_bytes() for rel in port.KEEP} == kept


def test_dry_run_lists_files_without_changing_them(tmp_path, capsys):
    root = copy_repo(tmp_path)
    before = snapshot(root)

    assert port.main(["--root", str(root), "--repo", NEW_REPO, "--dry-run"]) == 0

    assert snapshot(root) == before
    assert "README.md" in capsys.readouterr().out


@pytest.mark.parametrize("args", [
    ["--repo", "acme/../x"],
    ["--repo", "acme"],
    ["--repo", NEW_REPO, "--server-url", "http://ghe.example.com"],
    ["--repo", NEW_REPO, "--server-url", 'https://ghe"x.example.com'],
    ["--repo", NEW_REPO, "--server-url", "https://ghe\\x.example.com"],
    ["--repo", NEW_REPO, "--server-url", "https://github.com:443"],
    ["--repo", NEW_REPO, "--pages-url", "ftp://pages.example.com/x"],
    ["--repo", NEW_REPO, "--from", "https://github.com/only-an-owner"],
])
def test_bad_values_change_nothing(tmp_path, capsys, args):
    root = copy_repo(tmp_path)
    before = snapshot(root)

    assert port.main(["--root", str(root), *args]) == 1

    assert snapshot(root) == before
    assert capsys.readouterr().err.startswith("error:")


def test_without_pages_url_the_old_download_page_is_reported(tmp_path, capsys):
    if old_pages_host() is None:
        pytest.skip("only a github.com repository has a known Pages address")
    root = copy_repo(tmp_path)

    assert port.main(["--root", str(root), "--repo", NEW_REPO]) == 0

    out = capsys.readouterr().out
    assert "--pages-url" in out and "docs/DISTRIBUTION.md" in out


def test_porting_guide_lists_every_file_that_names_the_repository():
    guide = (ROOT / "docs" / "PORTING.md").read_text(encoding="utf-8")
    missing = [rel for path in port.candidate_files(ROOT)
               if (rel := path.relative_to(ROOT).as_posix()) not in port.KEEP
               and names_the_repository(path.read_text(encoding="utf-8")) and f"`{rel}`" not in guide]
    assert missing == []


def test_tests_follow_the_app_instead_of_naming_the_repository():
    """Tests read the repository from snipforge/project.py, so they still pass after a port."""
    for path in sorted((ROOT / "tests").rglob("*.py")):
        assert not names_the_repository(path.read_text(encoding="utf-8")), path.name
