"""Where SnipForge lives online (snipforge/project.py): one place for the repository, its server,
the API address and the hosts release downloads may come from."""
import pytest

from snipforge import project


def test_defaults_are_consistent():
    assert project.REPO_URL == f"{project.SERVER_URL}/{project.REPO}"
    assert project.RELEASES_URL == f"{project.REPO_URL}/releases"
    assert project.API_URL == project.api_url(project.SERVER_URL)
    assert project.DOWNLOAD_HOSTS == project.download_hosts(project.SERVER_URL)
    assert project.REPO_RE.fullmatch(project.REPO)


@pytest.mark.parametrize("server, api", [
    ("https://github.com", "https://api.github.com"),
    ("https://GitHub.com/", "https://api.github.com"),
    ("https://acme.ghe.com", "https://api.acme.ghe.com"),
    ("https://ghe.example.com", "https://ghe.example.com/api/v3"),
    ("https://ghe.example.com:8443", "https://ghe.example.com:8443/api/v3"),
])
def test_api_address_follows_the_server(server, api):
    assert project.api_url(server) == api


def test_github_com_downloads_may_come_from_its_file_storage():
    hosts = project.download_hosts("https://github.com")
    assert {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"} <= hosts


def test_enterprise_downloads_come_from_the_server_and_listed_extras():
    assert project.download_hosts("https://ghe.example.com", ()) == {"ghe.example.com"}
    assert project.download_hosts("https://ghe.example.com", ("Media.GHE.example.com",)) == {
        "ghe.example.com", "media.ghe.example.com"}
    assert "objects.githubusercontent.com" not in project.download_hosts("https://acme.ghe.com", ())


def test_repo_url_has_no_doubled_slash():
    assert project.repo_url("https://ghe.example.com/", "acme/snipforge") == "https://ghe.example.com/acme/snipforge"


@pytest.mark.parametrize("bad", ["http://github.com", "github.com", "https://", "https://user:pw@ghe.example.com",
                                 "https://ghe.example.com/github", "https://ghe.example.com/?x=1", "", None])
def test_server_must_be_a_bare_https_address(bad):
    with pytest.raises(ValueError):
        project.server_host(bad)


@pytest.mark.parametrize("bad", ["acme", "acme/snip/forge", "../snipforge", "acme/..", "acme/snip forge", ""])
def test_repo_must_be_owner_slash_name(bad):
    with pytest.raises(ValueError):
        project.check_repo(bad)
