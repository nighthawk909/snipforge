"""Build the download page for GitHub Pages: docs/site plus the current release's details.

    gh api "repos/<owner>/<repo>/releases?per_page=50" > releases.json
    python scripts/build_site.py --releases releases.json --site docs/site --out _site

docs/site/index.html works on its own (opened as a file, uploaded to SharePoint). This fills its
<!-- generated:NAME --> ... <!-- /generated:NAME --> markers with the latest stable version, date,
size and release notes, plus a download for a newer preview build when there is one, and points
every link at the repository it's built for (--server-url and --repo; snipforge/project.py's by
default). Nothing is fetched when someone views the page: .github/workflows/pages.yml rebuilds it
after every release. Needs only the Python standard library.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_feed import TAG_RE, project, version_key  # noqa: E402 - sibling script, not a package

MAX_NOTES = 6
# release-please sections people care about; refactors, tests and CI stay out of the page.
NOTE_SECTIONS = ("Features", "Bug fixes", "Performance")
LATEST_ALIAS = "SnipForge-setup.exe"
INSTALLER = "SnipForge-{version}-setup.exe"
MAC_ALIAS = "SnipForge.dmg"  # the version-less copy behind the permanent Mac download link
MAC_INSTALLER = "SnipForge-{version}.dmg"
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")


class SiteError(Exception):
    """The release data or the page template can't produce a page."""


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    preview: bool
    published: datetime | None
    assets: dict[str, int]   # name -> size in bytes
    body: str

    @property
    def installer(self) -> str:
        return INSTALLER.format(version=self.version)

    @property
    def mac_installer(self) -> str:
        return MAC_INSTALLER.format(version=self.version)

    @property
    def ready(self) -> bool:
        """Its installer is attached (release-please publishes first; the build attaches files later)."""
        return self.installer in self.assets


# ------------------------------------------------------------------ release data


def parse_releases(data: object) -> list[Release]:
    """Published (non-draft) releases with a SnipForge version tag."""
    if not isinstance(data, list):
        raise SiteError("expected the JSON list from GET /repos/{owner}/{repo}/releases")
    releases = []
    for item in data:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        match = TAG_RE.fullmatch(str(item.get("tag_name", "")))
        if not match:
            continue
        version = match.group(1)
        assets = {str(a.get("name", "")): int(a.get("size") or 0)
                  for a in item.get("assets") or [] if isinstance(a, dict)}
        preview = bool(item.get("prerelease")) or version_key(version)[3] == 0
        releases.append(Release(version, f"v{version}", preview, parse_date(item.get("published_at")), assets,
                                str(item.get("body") or "")))
    return releases


def parse_date(text: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None


def pick(releases: list[Release]) -> tuple[Release, Release | None]:
    """The newest stable release that has its installer, and a newer preview if there is one."""
    stable = [r for r in releases if not r.preview and r.ready]
    if not stable:
        raise SiteError("there's no published stable release with an installer to point the page at")
    latest = max(stable, key=lambda r: version_key(r.version))
    previews = [r for r in releases if r.preview and r.ready and version_key(r.version) > version_key(latest.version)]
    return latest, max(previews, key=lambda r: version_key(r.version)) if previews else None


def release_notes(body: str) -> list[str]:
    """The bullet points under Features / Bug fixes / Performance in release-please notes."""
    items: list[str] = []
    section, continuing = None, False
    for line in body.replace("\r\n", "\n").split("\n"):
        heading = re.match(r"#{2,4}\s+(.+)", line)
        if heading:
            section, continuing = heading.group(1).strip(), False
            continue
        if section not in NOTE_SECTIONS:
            continue
        bullet = re.match(r"[*-]\s+(.+)", line)
        if bullet:
            items = [*items, bullet.group(1)]
            continuing = True
        elif continuing and line.startswith("  ") and line.strip():
            items = [*items[:-1], f"{items[-1]} {line.strip()}"]
        else:
            continuing = False
    return [note for note in (clean_note(i) for i in items) if note]


def clean_note(text: str) -> str:
    """'**crop:** Esc cancels ([#3](...)) ([abc123](...))' -> 'Crop: Esc cancels'."""
    text = re.sub(r"\s*\(\[[^\]]*\]\([^)]*\)\)", "", text)   # issue and commit references
    text = re.sub(r"^\*\*([^*]+?):\*\*\s*", lambda m: f"{m.group(1)}: ", text)  # **scope:**
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # [text](url) -> text
    text = " ".join(text.replace("**", "").replace("`", "").split())
    return text[:1].upper() + text[1:]


def long_date(moment: datetime | None) -> str:
    return f"{MONTHS[moment.month - 1]} {moment.day}, {moment.year}" if moment else ""


# ------------------------------------------------------------------ page


def replace_marker(page: str, name: str, content: str) -> str:
    pattern = re.compile(rf"(<!-- generated:{name} -->).*?(<!-- /generated:{name} -->)", re.DOTALL)
    if not pattern.search(page):
        raise SiteError(f"docs/site/index.html is missing the <!-- generated:{name} --> marker")
    return pattern.sub(lambda m: m.group(1) + content + m.group(2), page, count=1)


def point_at_repository(page: str, url: str) -> str:
    """The template links to its own repository (the brand link at the top); a copy elsewhere (say
    on GitHub Enterprise) links to that copy instead."""
    brand = re.search(r'<a\b[^>]*\bclass="brand"[^>]*\bhref="([^"]+)"', page)
    if not brand:
        raise SiteError('docs/site/index.html has no <a class="brand" href="..."> link to the repository')
    own = html.unescape(brand.group(1)).rstrip("/")
    return page if own == url else page.replace(html.escape(own), html.escape(url))


def set_download_link(page: str, url: str) -> str:
    pattern = re.compile(r'(<a\b[^>]*\bid="download"[^>]*\bhref=")[^"]*(")')
    if not pattern.search(page):
        raise SiteError('docs/site/index.html has no <a id="download" href="..."> button')
    return pattern.sub(lambda m: m.group(1) + html.escape(url) + m.group(2), page, count=1)


def whats_new(release: Release, release_url: str) -> str:
    notes = release_notes(release.body)
    shown, more = notes[:MAX_NOTES], len(notes) - MAX_NOTES
    items = "".join(f"<li>{html.escape(note)}</li>" for note in shown)
    link = f'<a href="{html.escape(release_url)}">release notes</a>'
    footer = (f"<p>Plus {more} more in the {link}.</p>" if more > 0
              else f"<p>Everything that changed is in the {link}.</p>")
    released = f'<p class="released">Released {long_date(release.published)}</p>' if release.published else ""
    return (f'\n  <section id="whats-new">\n    <div class="wrap">\n'
            f"      <h2>What's new in {html.escape(release.version)}</h2>\n      {released}\n"
            + (f'      <ul class="notes">{items}</ul>\n' if items else "")
            + f"      {footer}\n    </div>\n  </section>\n  ")


def preview_card(release: Release, installer_url: str, release_url: str) -> str:
    when = f" from {long_date(release.published)}" if release.published else ""
    size = round(release.assets[release.installer] / 1_048_576)
    return (f'\n      <div class="preview" id="preview">'
            f"<h3>Try what's next: SnipForge {html.escape(release.version)}</h3>"
            f"<p>A preview build{when} with features that haven't reached everyone yet. It's for testing,"
            f" so expect the odd rough edge; the next stable release replaces it.</p>"
            f'<p><a class="button-secondary" href="{html.escape(installer_url)}">Download the preview ({size} MB)</a>'
            f' &nbsp; <a href="{html.escape(release_url)}">What\'s in it</a></p></div>\n      ')


def mac_download(release: Release, base: str, permanent: bool, repo_url: str) -> str:
    """The line under the Windows button: the Mac .dmg when the release has one."""
    parity = f'<a href="{html.escape(repo_url)}/blob/main/docs/PARITY.md">what works on a Mac</a>'
    if release.mac_installer not in release.assets:
        return f"On a Mac? A Mac version is on the way: see {parity} so far."
    url = (f"{base}/latest/download/{MAC_ALIAS}" if permanent and MAC_ALIAS in release.assets
           else f"{base}/download/{release.tag}/{release.mac_installer}")
    size = round(release.assets[release.mac_installer] / 1_048_576)
    return (f'On a Mac? <a id="download-mac" href="{html.escape(url)}">Download for Mac</a> ({size} MB), '
            f'macOS 14 or later · {parity} · <a href="#mac">the first launch</a>')


def render(template: str, releases: object, repo: str, server_url: str = project.SERVER_URL) -> str:
    try:
        repo_url = project.repo_url(server_url, repo)
    except ValueError as e:
        raise SiteError(str(e)) from e
    base = f"{repo_url}/releases"
    published = parse_releases(releases)
    stable, preview = pick(published)
    # .../latest/download/ follows GitHub's newest stable release, so use it only when that's the one
    # shown here and it carries the version-less copy; otherwise link this version's installer.
    newest = max((r for r in published if not r.preview), key=lambda r: version_key(r.version))
    permanent = newest is stable and LATEST_ALIAS in stable.assets
    download = (f"{base}/latest/download/{LATEST_ALIAS}" if permanent
                else f"{base}/download/{stable.tag}/{stable.installer}")
    size = round(stable.assets[stable.installer] / 1_048_576)
    note = " · ".join(part for part in (f"Version {stable.version}", long_date(stable.published), f"{size} MB") if part)

    page = set_download_link(point_at_repository(template, repo_url), download)
    page = replace_marker(page, "download-note", html.escape(note))
    page = replace_marker(page, "mac-download", mac_download(stable, base, permanent, repo_url))
    page = replace_marker(page, "whats-new", whats_new(stable, f"{base}/tag/{stable.tag}"))
    card = (preview_card(preview, f"{base}/download/{preview.tag}/{preview.installer}", f"{base}/tag/{preview.tag}")
            if preview else "")
    return replace_marker(page, "preview", card)


# ------------------------------------------------------------------ command line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--releases", type=Path, required=True,
                        help='JSON from gh api "repos/<owner>/<repo>/releases?per_page=50"')
    parser.add_argument("--site", type=Path, default=Path("docs/site"), help="the page's source folder")
    parser.add_argument("--out", type=Path, required=True, help="output folder (must be new or empty)")
    parser.add_argument("--repo", default=project.REPO,
                        help=f"the repository (default {project.REPO}, from snipforge/project.py)")
    parser.add_argument("--server-url", default=project.SERVER_URL,
                        help=f"the GitHub server (default {project.SERVER_URL}); in Actions, pass $GITHUB_SERVER_URL")
    args = parser.parse_args(argv)
    try:
        releases = json.loads(args.releases.read_text(encoding="utf-8-sig"))
        page = render((args.site / "index.html").read_text(encoding="utf-8"), releases, args.repo, args.server_url)
        if args.out.exists() and any(args.out.iterdir()):
            raise SiteError(f"{args.out} isn't empty; give a new folder")
        shutil.copytree(args.site, args.out, dirs_exist_ok=True)
        (args.out / "index.html").write_text(page, encoding="utf-8", newline="\n")
    except (SiteError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"Built {args.out / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
