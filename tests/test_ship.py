"""scripts/ship.py: merges the release PR once it's green and up to date, then checks the release."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from snipforge import project

ROOT = Path(__file__).resolve().parents[1]
REPO = project.REPO
HEAD, MAIN, MERGED = "a" * 40, "b" * 40, "c" * 40
LINK = f"{project.RELEASES_URL}/download/v0.18.0/SnipForge-setup.exe"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ship = _load("ship")


class Seq:
    """Answers in turn, the last one again after that."""

    def __init__(self, *answers):
        self.answers = list(answers)

    def __call__(self, _args):
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class FakeGh:
    """Answers gh calls from {args start: answer}; an answer may be a value, an exception or a
    function of the args. Records every call. `release download` writes `files`."""

    def __init__(self, files=None, **changes):
        self.files, self.calls = files if files is not None else release_files(), []
        self.answers = {
            ("pr", "list", "--repo", REPO, "--state", "open", "--label"): [RELEASE_PR],
            ("pr", "list", "--repo", REPO, "--state", "open", "--base"): [],
            ("api", f"repos/{REPO}/commits/main"): MAIN,
            ("run", "list"): self._runs,
            ("api", f"repos/{REPO}/git/ref/heads/release-please--branches--main--components--snipforge"): HEAD,
            ("pr", "checks"): [{"name": "Lint and test", "bucket": "pass"},
                               {"name": "Build installer / Build and self-test", "bucket": "pass"}],
            ("api", f"repos/{REPO}/rules/branches/main"): RULES,
            ("pr", "merge"): "",
            ("api", f"repos/{REPO}/pulls/134"): MERGED,
            ("release", "view"): published(),
            ("release", "list"): [{"tagName": "v0.17.0", "isDraft": False}],
            ("api", f"repos/{REPO}/releases/latest"): "v0.18.0",
            ("repo", "view"): {"isPrivate": False},
            ("attestation", "verify"): "",
        }
        self.main_runs = [{"status": "completed", "conclusion": "success", "url": "https://example/run/1"}]
        self.release_runs = [{"status": "in_progress", "conclusion": "", "url": "https://example/run/2"}]
        for key, value in changes.items():
            setattr(self, key, value) if key.endswith("_runs") else self.answers.update({KEYS[key]: value})

    def _runs(self, args):
        return self.release_runs if args[args.index("--commit") + 1] == MERGED else self.main_runs

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["release", "download"]:
            folder = Path(args[args.index("--dir") + 1])
            for name, data in self.files.items():
                (folder / name).write_bytes(data)
            return ""
        for start, answer in self.answers.items():
            if tuple(args[:len(start)]) == start:
                answer = answer(args) if callable(answer) else answer
                if isinstance(answer, Exception):
                    raise answer
                return answer if isinstance(answer, str) else json.dumps(answer)
        raise AssertionError(f"unexpected gh call: {args}")

    def ran(self, *start):
        return [c for c in self.calls if c[:len(start)] == list(start)]


RELEASE_PR = {"number": 134, "title": "chore(main): release 0.18.0", "headRefOid": HEAD,
              "headRefName": "release-please--branches--main--components--snipforge"}
RULES = [{"type": "deletion"}, {"type": "required_status_checks", "parameters": {"required_status_checks": [
    {"context": "Lint and test"}, {"context": "Build installer / Build and self-test"}]}}]
KEYS = {"release_pr": ("pr", "list", "--repo", REPO, "--state", "open", "--label"),
        "auto": ("pr", "list", "--repo", REPO, "--state", "open", "--base"),
        "main": ("api", f"repos/{REPO}/commits/main"),
        "head": ("api", f"repos/{REPO}/git/ref/heads/release-please--branches--main--components--snipforge"),
        "checks": ("pr", "checks"), "merge": ("pr", "merge"), "merge_sha": ("api", f"repos/{REPO}/pulls/134"),
        "release": ("release", "view"), "releases": ("release", "list"),
        "latest": ("api", f"repos/{REPO}/releases/latest"), "repo": ("repo", "view"),
        "attestation": ("attestation", "verify")}


def release_files(version="0.18.0", payload=b"installer", digest=None, crlf=False):
    digest = digest or hashlib.sha256(payload).hexdigest()
    end = "\r\n" if crlf else "\n"
    sums = f"{digest}  SnipForge-{version}-setup.exe{end}{digest} *SnipForge-setup.exe{end}"
    return {f"SnipForge-{version}-setup.exe": payload, "SnipForge-setup.exe": payload, "SHA256SUMS.txt": sums.encode()}


def published(version="0.18.0", draft=False, assets=None):
    names = assets if assets is not None else sorted(ship.expected_assets(version))
    return {"isDraft": draft, "assets": [{"name": n} for n in names]}


def run_ship(gh, tmp_path, **kw):
    naps = kw.pop("naps", [])
    return ship.ship(gh, REPO, workdir=tmp_path, location_of=lambda _url: LINK, sleep=naps.append, **kw)


# ---------------------------------------------------------------- before merging
def test_the_version_and_tag_are_checked():
    assert ship.version_of("chore(main): release 0.18.0") == "0.18.0"
    with pytest.raises(ship.ShipError):
        ship.version_of("feat: something else")
    with pytest.raises(ship.ShipError):
        ship.check_tag("../v1")


def test_nothing_to_ship_without_a_release_pr(tmp_path):
    gh = FakeGh(release_pr=[])
    assert run_ship(gh, tmp_path) is None and not gh.ran("pr", "merge")


def test_a_release_left_as_a_draft_is_reported_not_called_nothing(tmp_path):
    """Shipped once and interrupted after merging: the release may still be a draft."""
    gh = FakeGh(release_pr=[], releases=[{"tagName": "v0.18.0", "isDraft": True}])
    with pytest.raises(ship.ShipError, match="v0.18.0 is still a draft"):
        run_ship(gh, tmp_path)


def test_waits_for_prs_still_set_to_merge(tmp_path):
    gh = FakeGh(auto=[{"number": 141, "title": "feat: x", "autoMergeRequest": {"mergeMethod": "SQUASH"}},
                      {"number": 125, "title": "deps", "autoMergeRequest": None}])
    with pytest.raises(ship.ShipError, match="#141"):
        run_ship(gh, tmp_path)
    assert not gh.ran("pr", "merge")
    auto = next(c for c in gh.calls if c[:2] == ["pr", "list"] and "--base" in c)
    assert auto[auto.index("--base") + 1] == "main" and int(auto[auto.index("--limit") + 1]) >= 200


def test_a_failed_check_stops_it_and_says_which(tmp_path):
    gh = FakeGh(checks=[{"name": "Lint and test", "bucket": "fail"},
                        {"name": "Build installer / Build and self-test", "bucket": "pass"}])
    with pytest.raises(ship.ShipError, match="Lint and test"):
        run_ship(gh, tmp_path, wait=True)
    assert not gh.ran("pr", "merge")


@pytest.mark.parametrize(("checks", "says"), [
    ([{"name": "Lint and test", "bucket": "pending"}], "still running"),
    ([{"name": "Lint and test", "bucket": "pass"}], "hasn't started"),  # a required check not reported yet
    (ship.ShipError("no required checks reported on the 'x' branch"), "hasn't started"),
])
def test_unfinished_checks_stop_it_unless_asked_to_wait(tmp_path, checks, says):
    gh = FakeGh(checks=checks)
    with pytest.raises(ship.ShipError, match=says):
        run_ship(gh, tmp_path)
    assert not gh.ran("pr", "merge")


def test_waits_until_release_please_has_caught_up_with_main(tmp_path):
    """Merged before release-please updates the PR for the newest commit, the changelog misses it."""
    gh = FakeGh(main_runs=[{"status": "in_progress", "conclusion": "", "url": "u"}])
    with pytest.raises(ship.ShipError, match="release-please hasn't updated"):
        run_ship(gh, tmp_path)
    run_list = gh.ran("run", "list")[0]
    assert run_list[run_list.index("--event") + 1] == "push"  # not a run started by hand
    assert not gh.ran("pr", "merge")


def test_a_failed_release_please_run_stops_it(tmp_path):
    gh = FakeGh(main_runs=[{"status": "completed", "conclusion": "failure", "url": "https://example/run/7"}])
    with pytest.raises(ship.ShipError, match="run/7"):
        run_ship(gh, tmp_path, wait=True)


def test_a_release_pr_github_still_reports_at_its_old_commit_waits(tmp_path):
    gh = FakeGh(head="d" * 40)
    with pytest.raises(ship.ShipError, match="just been updated"):
        run_ship(gh, tmp_path)
    assert not gh.ran("pr", "merge")


def test_main_moving_on_during_the_checks_waits(tmp_path):
    gh = FakeGh(main=Seq(MAIN, "e" * 40))
    with pytest.raises(ship.ShipError, match="main moved on"):
        run_ship(gh, tmp_path)
    assert not gh.ran("pr", "merge")


def test_with_wait_it_waits_then_ships(tmp_path):
    pending = [{"name": "Lint and test", "bucket": "pending"},
               {"name": "Build installer / Build and self-test", "bucket": "pending"}]
    passed = [{**c, "bucket": "pass"} for c in pending]
    gh = FakeGh(checks=Seq(pending, pending, passed), release=Seq(published(draft=True), published()))
    naps = []
    assert run_ship(gh, tmp_path, wait=True, naps=naps) == "v0.18.0"
    assert len(naps) >= 3 and len(gh.ran("pr", "merge")) == 1


def test_waiting_gives_up_after_the_timeout(tmp_path):
    gh = FakeGh(checks=[{"name": "Lint and test", "bucket": "pending"}])
    with pytest.raises(ship.ShipError, match="Gave up waiting"):
        run_ship(gh, tmp_path, wait=True, timeout_s=-1)


def test_a_refused_merge_is_retried_when_waiting(tmp_path):
    gh = FakeGh(merge=Seq(ship.ShipError("Head branch was modified"), ""))
    assert run_ship(gh, tmp_path, wait=True) == "v0.18.0"
    assert len(gh.ran("pr", "merge")) == 2


# ---------------------------------------------------------------- merging and after
def test_merges_the_commit_it_checked_and_verifies_the_release(tmp_path):
    gh = FakeGh()
    assert run_ship(gh, tmp_path) == "v0.18.0"
    (merge,) = gh.ran("pr", "merge")
    assert merge[2] == "134" and "--squash" in merge and merge[merge.index("--match-head-commit") + 1] == HEAD
    attested = gh.ran("attestation", "verify")
    assert len(attested) == 2 and all("--signer-workflow" in c for c in attested)  # both installers


def test_a_failed_release_run_says_where_to_look(tmp_path):
    gh = FakeGh(release=published(draft=True),
                release_runs=[{"status": "completed", "conclusion": "failure", "url": "https://example/run/9"}])
    with pytest.raises(ship.ShipError, match="run/9"):
        run_ship(gh, tmp_path)


def test_a_cancelled_release_run_isnt_a_failure(tmp_path):
    """The release concurrency group cancels a queued run when a newer one queues; that one publishes."""
    gh = FakeGh(release=Seq(published(draft=True), published(draft=True), published()),
                release_runs=[{"status": "completed", "conclusion": "cancelled", "url": "u"}])
    assert run_ship(gh, tmp_path) == "v0.18.0"


def test_a_run_that_finished_without_publishing_is_reported(tmp_path):
    gh = FakeGh(release=published(draft=True),
                release_runs=[{"status": "completed", "conclusion": "success", "url": "https://example/run/3"}])
    with pytest.raises(ship.ShipError, match="finished but v0.18.0 isn't published"):
        run_ship(gh, tmp_path)


def test_no_release_yet_is_waited_for_but_other_errors_give_up(tmp_path):
    gh = FakeGh(release=Seq(ship.NotFound("release not found"), published()))
    assert run_ship(gh, tmp_path) == "v0.18.0"
    broken = FakeGh(release=ship.ShipError("HTTP 502"))
    with pytest.raises(ship.ShipError, match="keeps failing.*verify v0.18.0"):
        run_ship(broken, tmp_path)


def test_a_merge_commit_github_hasnt_given_yet_is_waited_for(tmp_path):
    gh = FakeGh(merge_sha=Seq("", MERGED), release=Seq(published(draft=True), published()))
    assert run_ship(gh, tmp_path) == "v0.18.0"


def test_latest_lagging_a_moment_behind_publishing_is_given_time(tmp_path):
    gh = FakeGh(latest=Seq("v0.17.0", "v0.18.0"))
    assert run_ship(gh, tmp_path) == "v0.18.0"


# ---------------------------------------------------------------- verify
def test_verify_passes_a_good_release_with_crlf_sums(tmp_path):
    gh = FakeGh(files=release_files(crlf=True))
    assert ship.verify(gh, REPO, "v0.18.0", tmp_path, lambda _url: LINK) == []


def test_verify_catches_a_draft_missing_files_and_bad_checksums(tmp_path):
    gh = FakeGh(files=release_files(digest="0" * 64),
                release=published(draft=True, assets=["SnipForge-0.18.0-setup.exe", "SHA256SUMS.txt"]))
    problems = "\n".join(ship.verify(gh, REPO, "v0.18.0", tmp_path, lambda _url: LINK))
    assert "draft" in problems and "SnipForge-0.18.0-portable.zip" in problems and "sbom.cdx.json" in problems
    assert "SnipForge-0.18.0-setup.exe's checksum" in problems and "SnipForge-setup.exe's checksum" in problems


def test_verify_catches_an_older_latest_and_a_stale_download_link(tmp_path):
    gh = FakeGh(latest="v0.17.0")
    problems = "\n".join(ship.verify(gh, REPO, "v0.18.0", tmp_path, lambda url: url.replace("latest", "v0.17.0")))
    assert "Latest is v0.17.0" in problems and "download link" in problems


def test_verify_catches_a_failed_attestation_and_skips_it_for_a_private_repo(tmp_path):
    gh = FakeGh(attestation=ship.ShipError("no attestation found"))
    assert any("attestation" in p for p in ship.verify(gh, REPO, "v0.18.0", tmp_path, lambda _url: LINK))
    private = FakeGh(attestation=ship.ShipError("no attestation found"), repo={"isPrivate": True})
    assert ship.verify(private, REPO, "v0.18.0", tmp_path, lambda _url: LINK) == []


def test_verify_says_when_theres_no_such_release(tmp_path):
    gh = FakeGh(release=ship.NotFound("release not found"))
    assert ship.verify(gh, REPO, "v0.18.0", tmp_path, lambda _url: LINK) == ["there's no release v0.18.0"]


def test_location_of_only_checks_https_links():
    with pytest.raises(ship.ShipError):
        ship.location_of("file:///C:/Windows/win.ini")


def test_the_command_line_exits_1_with_whats_wrong(capsys):
    assert ship.main(["verify", "v0.18.0"], gh=FakeGh(latest="v0.17.0"), link=lambda _url: LINK) == 1
    assert "Latest is v0.17.0" in capsys.readouterr().err
    assert ship.main(["verify", "nope"], gh=FakeGh(), link=lambda _url: LINK) == 1
    assert ship.main(["verify", "v0.18.0"], gh=FakeGh(), link=lambda _url: LINK) == 0
    assert ship.main(["status"], gh=FakeGh(), link=lambda _url: LINK) == 0
    assert "ready" in capsys.readouterr().out
