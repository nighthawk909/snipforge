"""Where SnipForge lives online: its repository and the GitHub server that hosts it.

This is the only place in the app that names them. To move SnipForge (for example into a copy on
GitHub Enterprise), run scripts/port_repo.py, which rewrites SERVER_URL and REPO here along with
the docs; see docs/PORTING.md. The API address and the hosts release downloads may come from follow
from SERVER_URL. Standard library only: scripts/ import this too.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

SERVER_URL = "https://github.com"
REPO = "nighthawk909/snipforge"
# Hosts besides SERVER_URL's own that release downloads may redirect to. github.com's file storage
# is added automatically; on GitHub Enterprise, add the host your server hands downloads to, if any.
EXTRA_DOWNLOAD_HOSTS: tuple[str, ...] = ()

GITHUB_COM_DOWNLOAD_HOSTS = ("objects.githubusercontent.com", "release-assets.githubusercontent.com")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def _origin(server_url) -> tuple[str, str]:
    """('https://ghe.example.com', 'ghe.example.com'). Raises ValueError unless it's a bare https address."""
    parsed = urlparse(server_url if isinstance(server_url, str) else "")
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment):
        raise ValueError(f"The GitHub server must be an https:// address like https://github.com, not {server_url!r}")
    return f"https://{parsed.netloc.lower()}", parsed.hostname


def server_host(server_url: str) -> str:
    return _origin(server_url)[1]


def check_repo(repo: str) -> str:
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo) or ".." in repo:
        raise ValueError(f"The repository must look like owner/name, not {repo!r}")
    return repo


def repo_url(server_url: str, repo: str) -> str:
    return f"{_origin(server_url)[0]}/{check_repo(repo)}"


def api_url(server_url: str) -> str:
    """The REST API root: api.github.com, api.<name>.ghe.com, or <server>/api/v3 (GitHub Enterprise Server)."""
    origin, host = _origin(server_url)
    if host == "github.com":
        return "https://api.github.com"
    if host.endswith(".ghe.com"):
        return f"https://api.{host}"
    return f"{origin}/api/v3"


def download_hosts(server_url: str, extra: tuple[str, ...] = EXTRA_DOWNLOAD_HOSTS) -> frozenset[str]:
    host = server_host(server_url)
    storage = GITHUB_COM_DOWNLOAD_HOSTS if host == "github.com" else ()
    return frozenset({host, *storage, *(h.lower() for h in extra)})


REPO_URL = repo_url(SERVER_URL, REPO)
RELEASES_URL = f"{REPO_URL}/releases"
API_URL = api_url(SERVER_URL)
DOWNLOAD_HOSTS = download_hosts(SERVER_URL)
