"""Point a copy of SnipForge at its new home (for example a repository on GitHub Enterprise).

    python scripts/port_repo.py --repo acme/snipforge
    python scripts/port_repo.py --repo acme/snipforge --server-url https://ghe.example.com \\
        --pages-url https://pages.ghe.example.com/acme/snipforge/
    python scripts/port_repo.py --repo acme/snipforge --from https://github.com/<owner>/<repo>

Rewrites snipforge/project.py (SERVER_URL and REPO, which the app, scripts, installer and tests
read) and every link to the old repository in the docs, the download page, workflows and
pyproject.toml, in one pass. The old home is the one project.py names, or --from after merging in
later changes from the original repository. CHANGELOG.md (history) and docs/PORTING.md (which
describes the original) are left as they are. Review the result with `git diff`; see
docs/PORTING.md for the rest of the move. Needs only the Python standard library.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from snipforge import project  # noqa: E402 - standard library only

LOCATION_FILE = Path("snipforge") / "project.py"
KEEP = frozenset({"CHANGELOG.md", "docs/PORTING.md"})   # history, and the guide to the original
SKIP_DIRS = frozenset({".git", "dist", "build", "__pycache__", "node_modules"})
MAX_BYTES = 2_000_000
SERVER_LINE = re.compile(r'^SERVER_URL = "[^"\n]*"$', re.MULTILINE)
REPO_LINE = re.compile(r'^REPO = "[^"\n]*"$', re.MULTILINE)


class PortError(Exception):
    """Bad input or an unreadable copy; nothing was changed."""


@dataclass(frozen=True)
class Home:
    server_url: str
    repo: str

    @property
    def url(self) -> str:
        return project.repo_url(self.server_url, self.repo)

    @property
    def pages_url(self) -> str | None:
        """github.com serves a public repository's Pages at <owner>.github.io/<name>; other servers vary."""
        if project.server_host(self.server_url) != "github.com":
            return None
        owner, name = self.repo.split("/")
        return f"https://{owner.lower()}.github.io/{name}"


def home(server_url: str, repo: str) -> Home:
    try:
        project.server_host(server_url)
        project.check_repo(repo)
    except ValueError as e:
        raise PortError(str(e)) from e
    return Home(server_url.rstrip("/"), repo)


def home_from_url(url: str) -> Home:
    """'https://github.com/owner/name' -> Home('https://github.com', 'owner/name')."""
    parsed = urlparse(url or "")
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2:
        raise PortError(f"--from must be a repository address like https://github.com/owner/name, not {url!r}")
    return home(f"{parsed.scheme}://{parsed.netloc}", "/".join(parts))


def read_home(root: Path) -> Home:
    path = root / LOCATION_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise PortError(f"Couldn't read {path}: {e}") from e
    server, repo = SERVER_LINE.search(text), REPO_LINE.search(text)
    if not (server and repo):
        raise PortError(f"{path} has no SERVER_URL = \"...\" and REPO = \"...\" lines")
    return home(server.group(0).split('"')[1], repo.group(0).split('"')[1])


def no_scheme(url: str) -> str:
    return url.split("://", 1)[1]


def replacements(old: Home, new: Home, pages_url: str | None) -> dict[str, str]:
    """Old text -> new text, most specific first: API paths, Pages, repository links, bare owner/name."""
    pairs = {
        f"{no_scheme(project.api_url(old.server_url))}/repos/{old.repo}":
            f"{no_scheme(project.api_url(new.server_url))}/repos/{new.repo}",
        old.url: new.url,
        no_scheme(old.url): no_scheme(new.url),
        old.repo: new.repo,
    }
    if old.pages_url and pages_url:
        pairs = {old.pages_url: pages_url.rstrip("/"), no_scheme(old.pages_url): no_scheme(pages_url.rstrip("/")),
                 **pairs}
    return {k: v for k, v in pairs.items() if k != v}


def rewrite(text: str, pairs: dict[str, str]) -> str:
    """Replace in one pass, so a new name that contains the old one isn't replaced again."""
    if not pairs:
        return text
    pattern = re.compile("|".join(re.escape(k) for k in sorted(pairs, key=len, reverse=True)))
    return pattern.sub(lambda m: pairs[m.group(0)], text)


def candidate_files(root: Path) -> list[Path]:
    """Text files of the project: everything but hidden folders (except .github), builds and virtualenvs."""
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")
                         and (not d.startswith(".") or d == ".github")
                         and not (Path(folder) / d / "pyvenv.cfg").exists())
        for name in sorted(files):
            path = Path(folder) / name
            if name == ".git":  # a worktree's pointer to its repository
                continue
            if path.stat().st_size <= MAX_BYTES and is_text(path):
                found.append(path)
    return found


def is_text(path: Path) -> bool:
    try:
        path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    return True


def port(root: Path, new: Home, pages_url: str | None, old: Home | None = None,
         dry_run: bool = False) -> tuple[list[str], list[str]]:
    """Rewrite the copy at `root`. Returns (changed files, files still naming the old Pages address)."""
    old = old or read_home(root)
    pairs = replacements(old, new, pages_url)
    changed, pages_left = [], []
    for path in candidate_files(root):
        rel = path.relative_to(root).as_posix()
        if rel in KEEP:
            continue
        text = path.read_text(encoding="utf-8")
        updated = rewrite(text, pairs)
        if rel == LOCATION_FILE.as_posix():
            updated = SERVER_LINE.sub(f'SERVER_URL = "{new.server_url}"', updated, count=1)
            updated = REPO_LINE.sub(f'REPO = "{new.repo}"', updated, count=1)
        if old.pages_url and no_scheme(old.pages_url) in updated.lower():
            pages_left.append(rel)
        if updated != text:
            changed.append(rel)
            if not dry_run:
                with path.open("w", encoding="utf-8", newline="") as f:  # keep each file's line endings
                    f.write(updated)
    return changed, pages_left


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", required=True, help="the new repository, owner/name")
    parser.add_argument("--server-url", help="the new GitHub server, e.g. https://ghe.example.com "
                                             "(default: keep the current one)")
    parser.add_argument("--pages-url", help="the new download page address, if GitHub Pages is used there")
    parser.add_argument("--from", dest="from_url", metavar="REPO_URL",
                        help="the old repository's address (default: the one snipforge/project.py names)")
    parser.add_argument("--root", type=Path, default=ROOT, help="the copy to change (default: this one)")
    parser.add_argument("--dry-run", action="store_true", help="list the files that would change")
    args = parser.parse_args(argv)
    try:
        current = read_home(args.root)
        new = home(args.server_url or current.server_url, args.repo)
        old = home_from_url(args.from_url) if args.from_url else current
        if args.pages_url and urlparse(args.pages_url).scheme != "https":
            raise PortError(f"--pages-url must be an https:// address, not {args.pages_url!r}")
        changed, pages_left = port(args.root, new, args.pages_url, old, args.dry_run)
    except (PortError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    verb = "Would change" if args.dry_run else "Changed"
    print(f"{verb} {len(changed)} files to point at {new.url}:")
    for rel in changed:
        print(f"  {rel}")
    if pages_left:
        print(f"\nThese still link to the old download page {old.pages_url}/; give --pages-url, or edit them:")
        for rel in pages_left:
            print(f"  {rel}")
    if not args.dry_run:
        print("\nNext: review with `git diff`, run ruff and pytest, then follow docs/PORTING.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
