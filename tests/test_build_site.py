"""scripts/build_site.py: stamps the download page with the current release when Pages deploys it."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from snipforge import project

ROOT = Path(__file__).resolve().parents[1]
REPO = project.REPO_URL
PERMANENT_LINK = f"{REPO}/releases/latest/download/SnipForge-setup.exe"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


site = _load("build_site")

NOTES_060 = (
    f"## [0.6.0]({REPO}/compare/v0.5.0...v0.6.0) (2026-10-20)\n\n\n"
    "### Features\n\n"
    f"* **updates:** choose Stable or Preview updates ([#12]({REPO}/issues/12)) "
    f"([abc1234]({REPO}/commit/abc1234))\n"
    "* themes: light and dark\n  with a new accent color\n\n\n"
    "### Bug fixes\n\n"
    "* **crop:** Esc cancels a crop <script>alert(1)</script> ([def5678](https://github.com/x/commit/def5678))\n\n"
    "### Code improvements\n\n* refactor internals\n"
)


def asset(tag, name, size=29_155_538):
    return {"name": name, "size": size, "browser_download_url": f"{REPO}/releases/download/{tag}/{name}"}


def release(tag, prerelease=False, draft=False, body="", published="2026-10-20T15:00:00Z", alias=True):
    version = tag[1:]
    assets = [asset(tag, f"SnipForge-{version}-setup.exe"), asset(tag, "SHA256SUMS.txt", 300)]
    if alias:
        assets.append(asset(tag, "SnipForge-setup.exe"))
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft, "body": body,
            "published_at": published, "html_url": f"{REPO}/releases/tag/{tag}", "assets": assets}


TEMPLATE = (ROOT / "docs" / "site" / "index.html").read_text(encoding="utf-8")


def render(releases):
    return site.render(TEMPLATE, releases, project.REPO, project.SERVER_URL)


def test_download_note_shows_the_current_version_date_and_size():
    page = render([release("v0.6.0", body=NOTES_060), release("v0.5.0", published="2026-10-14T12:53:34Z")])
    assert "Version 0.6.0 · October 20, 2026 · 28 MB" in page
    assert f'id="download" href="{PERMANENT_LINK}"' in page


def test_latest_stable_is_the_highest_version_not_the_first_listed():
    page = render([release("v0.5.0", published="2026-10-21T00:00:00Z"), release("v0.6.0")])
    assert "Version 0.6.0" in page


def test_falls_back_to_the_versioned_installer_until_the_permanent_file_exists():
    page = render([release("v0.5.0", alias=False)])
    assert f'id="download" href="{REPO}/releases/download/v0.5.0/SnipForge-0.5.0-setup.exe"' in page


def test_while_a_new_release_is_still_building_the_page_keeps_the_previous_one():
    building = {**release("v0.6.0"), "assets": []}  # release-please published it; files not attached yet
    page = render([building, release("v0.5.0", published="2026-10-14T12:53:34Z")])
    assert "Version 0.5.0" in page
    # .../latest/download/ would hit 0.6.0, which has no installer yet, so link 0.5.0 directly.
    assert f'id="download" href="{REPO}/releases/download/v0.5.0/SnipForge-0.5.0-setup.exe"' in page


def test_whats_new_lists_user_facing_notes_cleanly():
    page = render([release("v0.6.0", body=NOTES_060)])
    section = page.split('id="whats-new"', 1)[1].split("</section>", 1)[0]
    assert "What's new in 0.6.0" in section
    assert "<li>Updates: choose Stable or Preview updates</li>" in section
    assert "<li>Themes: light and dark with a new accent color</li>" in section
    assert "Crop: Esc cancels a crop &lt;script&gt;alert(1)&lt;/script&gt;" in section
    assert "refactor internals" not in section  # hidden sections stay hidden
    assert "abc1234" not in section and "](" not in section
    assert f'href="{REPO}/releases/tag/v0.6.0"' in section


def test_whats_new_is_capped_and_links_to_the_rest():
    body = "### Features\n\n" + "".join(f"* feature {n}\n" for n in range(10))
    page = render([release("v0.6.0", body=body)])
    section = page.split('id="whats-new"', 1)[1].split("</section>", 1)[0]
    assert section.count("<li>") == site.MAX_NOTES
    assert "4 more" in section


def test_release_without_notes_still_links_to_them():
    page = render([release("v0.6.0", body="")])
    section = page.split('id="whats-new"', 1)[1].split("</section>", 1)[0]
    assert "<li>" not in section and f"{REPO}/releases/tag/v0.6.0" in section


def test_newer_preview_gets_its_own_download():
    preview = release("v0.7.0-preview.2", prerelease=True, published="2026-10-22T09:00:00Z", alias=False)
    older = release("v0.7.0-preview.1", prerelease=True, alias=False)
    page = render([preview, older, release("v0.6.0", body=NOTES_060)])
    card = page.split('id="preview"', 1)[1].split("</div>", 1)[0]
    assert "0.7.0-preview.2" in card and "October 22, 2026" in card
    assert f'href="{REPO}/releases/download/v0.7.0-preview.2/SnipForge-0.7.0-preview.2-setup.exe"' in card
    assert "0.7.0-preview.1" not in page


@pytest.mark.parametrize("candidate", [
    release("v0.6.0-preview.3", prerelease=True, alias=False),               # older than stable 0.6.0
    release("v0.7.0-preview.1", prerelease=True, draft=True, alias=False),   # unpublished draft
    {**release("v0.7.0-preview.1", prerelease=True, alias=False), "assets": []},  # no installer yet
])
def test_no_preview_card_without_a_usable_newer_preview(candidate):
    page = render([candidate, release("v0.6.0")])
    assert 'id="preview"' not in page


def test_drafts_and_odd_tags_are_never_the_stable_release():
    page = render([release("v0.9.0", draft=True), {**release("v0.8.0"), "tag_name": "nightly"}, release("v0.6.0")])
    assert "Version 0.6.0" in page


def test_no_stable_release_is_an_error():
    with pytest.raises(site.SiteError):
        render([release("v0.7.0-preview.1", prerelease=True)])


def test_missing_marker_is_an_error():
    with pytest.raises(site.SiteError, match="whats-new"):
        site.render(TEMPLATE.replace("<!-- generated:whats-new -->", ""), [release("v0.6.0")], project.REPO)


def test_generated_page_still_loads_nothing_from_elsewhere():
    page = render([release("v0.7.0-preview.1", prerelease=True, alias=False), release("v0.6.0", body=NOTES_060)])
    assert "<script" not in page
    for chunk in page.split('href="')[1:]:
        href = chunk.split('"', 1)[0]
        assert href.startswith((REPO, "#", "data:")), href


def test_page_points_at_the_repository_it_is_built_for():
    """Built in a copy on GitHub Enterprise, every link (the template's own ones too) goes to that copy."""
    ghe = "https://ghe.example.test/fixture-org/snipforge"
    page = site.render(TEMPLATE, [release("v0.7.0-preview.1", prerelease=True, alias=False),
                                  release("v0.6.0", body=NOTES_060)], "fixture-org/snipforge", "https://ghe.example.test")
    assert f'id="download" href="{ghe}/releases/latest/download/SnipForge-setup.exe"' in page
    assert f'{ghe}/releases/download/v0.7.0-preview.1/SnipForge-0.7.0-preview.1-setup.exe' in page
    for chunk in page.split('href="')[1:]:
        href = chunk.split('"', 1)[0]
        assert href.startswith((ghe, "#", "data:")), href
    assert REPO not in page


@pytest.mark.parametrize("server", ["http://ghe.example.test", "https://ghe.example.test/sub"])
def test_server_url_is_validated(server):
    with pytest.raises(site.SiteError):
        site.render(TEMPLATE, [release("v0.6.0")], "fixture-org/snipforge", server)


def test_cli_builds_the_site_folder(tmp_path):
    releases = tmp_path / "releases.json"
    releases.write_text(json.dumps([release("v0.6.0", body=NOTES_060)]), encoding="utf-8")
    out = tmp_path / "_site"

    assert site.main(["--releases", str(releases), "--site", str(ROOT / "docs" / "site"), "--out", str(out)]) == 0

    assert "Version 0.6.0" in (out / "index.html").read_text(encoding="utf-8")
    assert (out / "editor-preview.png").read_bytes() == (ROOT / "docs" / "site" / "editor-preview.png").read_bytes()


def test_cli_takes_the_server_and_repository(tmp_path):
    releases = tmp_path / "releases.json"
    releases.write_text(json.dumps([release("v0.6.0")]), encoding="utf-8")
    out = tmp_path / "_site"
    assert site.main(["--releases", str(releases), "--site", str(ROOT / "docs" / "site"), "--out", str(out),
                      "--repo", "fixture-org/snipforge", "--server-url", "https://ghe.example.test"]) == 0
    assert 'href="https://ghe.example.test/fixture-org/snipforge/releases/latest"' in (out / "index.html").read_text(
        encoding="utf-8")


def test_cli_refuses_a_non_empty_output_folder(tmp_path, capsys):
    releases = tmp_path / "releases.json"
    releases.write_text(json.dumps([release("v0.6.0")]), encoding="utf-8")
    out = tmp_path / "_site"
    out.mkdir()
    (out / "keep.txt").write_text("mine", encoding="utf-8")
    assert site.main(["--releases", str(releases), "--site", str(ROOT / "docs" / "site"), "--out", str(out)]) == 1
    assert (out / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert "error:" in capsys.readouterr().err


def test_cli_reports_bad_release_data(tmp_path):
    releases = tmp_path / "releases.json"
    releases.write_text("{not json", encoding="utf-8")
    assert site.main(["--releases", str(releases), "--site", str(ROOT / "docs" / "site"),
                      "--out", str(tmp_path / "_site")]) == 1


# --- Windows only ------------------------------------------------------------------------------------

def test_the_page_offers_only_the_windows_download():
    page = render([release("v0.12.0")])
    assert "Mac" not in page and ".dmg" not in page and "mac-download" not in page
