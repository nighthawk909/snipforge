"""The download page (docs/site) and install docs: one permanent link, and nothing loaded from elsewhere."""
from html.parser import HTMLParser
from pathlib import Path

import pytest

from snipforge import project

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "site"
REPO_URL = project.REPO_URL
PERMANENT_LINK = f"{REPO_URL}/releases/latest/download/SnipForge-setup.exe"


class Page(HTMLParser):
    def __init__(self, text: str):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def all(self, tag):
        return [attrs for t, attrs in self.tags if t == tag]


@pytest.fixture(scope="module")
def html():
    return (SITE / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page(html):
    return Page(html)


def test_big_download_button_uses_the_permanent_link(page):
    button = next(a for a in page.all("a") if a.get("id") == "download")
    assert button["href"] == PERMANENT_LINK


def test_page_loads_nothing_from_other_sites(page, html):
    assert page.all("script") == [] and page.all("iframe") == [] and page.all("object") == []
    assert "@import" not in html and "url(http" not in html
    for link in page.all("link"):
        assert link["href"].startswith("data:"), link  # icon inline; no external CSS or fonts
    for img in page.all("img"):
        assert (SITE / img["src"]).is_file(), img  # images sit next to the page


def test_links_only_go_to_the_project(page):
    for a in page.all("a"):
        href = a["href"]
        assert href.startswith((REPO_URL, "#")), href


def test_content_security_policy_blocks_third_parties(page):
    csp = next(m["content"] for m in page.all("meta") if m.get("http-equiv") == "Content-Security-Policy")
    assert "default-src 'none'" in csp and "script-src" not in csp


def test_page_covers_what_first_time_users_need(html):
    for text in ["Windows protected your PC", "More info", "Run anyway", "System requirements",
                 "Windows 10", "No admin rights", f"{REPO_URL}/releases/latest\"", "What it does"]:
        assert text in html, text


def test_readme_install_section_leads_with_the_permanent_link():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    install = readme.split("## Install", 1)[1].split("\n## ", 1)[0]
    first_step = next(line for line in install.splitlines() if line.strip())
    assert PERMANENT_LINK in first_step


def test_distribution_guide_documents_the_feed_policy():
    guide = (ROOT / "docs" / "DISTRIBUTION.md").read_text(encoding="utf-8")
    for text in [PERMANENT_LINK, "publish_feed.py", r"HKLM\Software\Policies\SnipForge", "UpdateSource",
                 '"feed"', "UpdateFeed", "Software Center", "/ALLUSERS"]:
        assert text in guide, text
