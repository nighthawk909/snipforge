"""Ship a release hands-off: merge release-please's PR once it's green and up to date, wait for the
Release workflow, then check the published release the way CLAUDE.md asks.

    python scripts/ship.py status            # the release PR, its checks, and what's still merging
    python scripts/ship.py release           # merge it now if it's ready, then check the release
    python scripts/ship.py release --wait    # wait for PRs set to auto-merge, checks and release-please first
    python scripts/ship.py verify v0.18.0    # check a published release again

`release` merges: run it only when the maintainer has said to ship. `status` and `verify` only read.

Before merging it checks that no other PR into main is still set to auto-merge (it would miss the
release), that release-please has updated the PR for main's newest commit (or the changelog misses
it), and that every check the "Protect main" ruleset requires passed. It merges the exact commit it
checked. After merging it waits for the release to be published, then checks: not a draft,
"Latest", every file attached, the permanent download link pointing at it, both installers matching
SHA256SUMS.txt, and their build attestations verifying. Uses the signed-in gh CLI and the standard
library only; exits 1 with what's wrong. Day to day, PRs ship with GitHub's auto-merge
(`gh pr merge <n> --squash --auto`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from snipforge import project  # noqa: E402 - standard library only; names the repository

RELEASE_LABEL = "autorelease: pending"
RELEASE_WORKFLOW = "release.yml"
TAG_RE = re.compile(r"v\d+\.\d+\.\d+")
FAILED = {"failure", "timed_out", "startup_failure", "action_required"}  # not cancelled: a newer run took over
POLL_S = 30
GH_TIMEOUT_S = 15 * 60  # downloading the installers is the slowest call
GH_RETRIES = 5  # gh errors in a row tolerated while waiting on a merged release


class ShipError(Exception):
    pass


class NotFound(ShipError):
    """gh says it doesn't exist (yet)."""


def run_gh(args: list[str]) -> str:
    env = {**os.environ, "GH_HOST": project.server_host(project.SERVER_URL)}  # GitHub Enterprise too
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", env=env,
                              timeout=GH_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise ShipError(f"gh {' '.join(args[:2])} took over {GH_TIMEOUT_S // 60} minutes") from exc
    if done.returncode:
        message = (done.stderr or done.stdout).strip() or f"gh {' '.join(args)} failed"
        raise (NotFound if re.search(r"not found|HTTP 404", message, re.IGNORECASE) else ShipError)(message)
    return done.stdout


def _json(gh, args: list[str]):
    return json.loads(gh(args) or "null")


def check_tag(tag: str) -> str:
    if not TAG_RE.fullmatch(tag):
        raise ShipError(f"A release tag looks like v1.2.3, not {tag!r}")
    return tag


def version_of(title: str) -> str:
    match = re.search(r"release (\d+\.\d+\.\d+)$", title.strip())
    if not match:
        raise ShipError(f"Can't read a version from the release PR's title: {title!r}")
    return match.group(1)


def expected_assets(version: str) -> set[str]:
    return {f"SnipForge-{version}-setup.exe", f"SnipForge-{version}-portable.zip", "SnipForge-setup.exe",
            "SHA256SUMS.txt", "sbom.cdx.json"}


def parse_sums(text: str) -> dict[str, str]:
    """SHA256SUMS.txt as {file name: digest}; CRLF lines and `*name` (binary mode) are fine."""
    sums = {}
    for line in text.splitlines():
        digest, _, name = line.strip().partition(" ")
        if name.strip():
            sums[name.strip().lstrip("*")] = digest.lower()
    return sums


def release_pr(gh, repo: str) -> dict | None:
    prs = _json(gh, ["pr", "list", "--repo", repo, "--state", "open", "--label", RELEASE_LABEL, "--limit", "5",
                     "--json", "number,title,headRefOid,headRefName"])
    return prs[0] if prs else None


def newest_draft(gh, repo: str) -> str | None:
    releases = _json(gh, ["release", "list", "--repo", repo, "--limit", "5", "--json", "tagName,isDraft"]) or []
    return next((r["tagName"] for r in releases if r["isDraft"]), None)


def _main_commit(gh, repo: str) -> str:
    return gh(["api", f"repos/{repo}/commits/main", "--jq", ".sha"]).strip()


def _runs(gh, repo: str, sha: str) -> list[dict]:
    """The Release workflow's runs for pushes of `sha` to main (not ones started by hand)."""
    return _json(gh, ["run", "list", "--repo", repo, "--workflow", RELEASE_WORKFLOW, "--event", "push",
                      "--commit", sha, "--json", "status,conclusion,url"]) or []


def required_checks(gh, repo: str) -> list[str]:
    rules = _json(gh, ["api", f"repos/{repo}/rules/branches/main"]) or []
    return [check["context"] for rule in rules if rule["type"] == "required_status_checks"
            for check in rule["parameters"]["required_status_checks"]]


def _check_buckets(gh, repo: str, number: int) -> dict[str, str]:
    try:
        checks = _json(gh, ["pr", "checks", str(number), "--repo", repo, "--required", "--json", "name,bucket"])
    except ShipError as exc:
        if "no required checks reported" in str(exc) or "no checks reported" in str(exc):
            return {}
        raise
    return {check["name"]: check["bucket"] for check in checks or []}


def blockers(gh, repo: str, pr: dict) -> tuple[list[str], list[str], str]:
    """(failures, still to come, main's commit) before `pr` can be merged."""
    failed, waiting = [], []
    for other in _json(gh, ["pr", "list", "--repo", repo, "--state", "open", "--base", "main", "--limit", "200",
                            "--json", "number,title,autoMergeRequest"]):
        if other.get("autoMergeRequest") and other["number"] != pr["number"]:
            waiting.append(f"#{other['number']} ({other['title']}) is still set to merge")
    main = _main_commit(gh, repo)
    done = [r for r in _runs(gh, repo, main) if r["status"] == "completed"]
    if broken := [r for r in done if r["conclusion"] in FAILED]:
        failed.append(f"release-please's run for main's newest commit failed: {broken[0]['url']}")
    elif not any(r["conclusion"] == "success" for r in done):
        waiting.append("release-please hasn't updated the release PR for main's newest commit yet")
    # For a few seconds after release-please updates the PR, GitHub reports its previous commit.
    head = gh(["api", f"repos/{repo}/git/ref/heads/{pr['headRefName']}", "--jq", ".object.sha"]).strip()
    if head != pr["headRefOid"]:
        waiting.append("the release PR has just been updated")
    buckets = _check_buckets(gh, repo, pr["number"])
    for name in required_checks(gh, repo):
        bucket = buckets.get(name)
        if bucket in ("fail", "cancel"):
            failed.append(f"the check {name!r} failed")
        elif bucket != "pass":
            waiting.append(f"the check {name!r} " + ("hasn't started" if bucket is None else "is still running"))
    return failed, waiting, main


def _until(deadline: float, sleep, what: str) -> None:
    if time.monotonic() > deadline:
        raise ShipError(f"Gave up waiting for {what}")
    sleep(POLL_S)


def ship(gh, repo: str, *, workdir: Path, location_of, sleep=time.sleep, wait: bool = False,
         timeout_s: float = 2 * 3600, publish_timeout_s: float = 3600) -> str | None:
    """Merge the release PR and check the release; returns its tag, or None if there's no release PR."""
    deadline = time.monotonic() + timeout_s
    while True:
        pr = release_pr(gh, repo)
        if pr is None:
            if draft := newest_draft(gh, repo):
                raise ShipError(f"No release PR is open, but {draft} is still a draft: check its Release run, "
                                f"then python scripts/ship.py verify {draft}")
            return None
        tag = f"v{version_of(pr['title'])}"
        failed, waiting, main = blockers(gh, repo, pr)
        if failed:
            raise ShipError("Not shipping: " + "; ".join(failed))
        if not waiting and _main_commit(gh, repo) != main:
            waiting = ["main moved on while it was being checked"]
        if not waiting:
            try:
                gh(["pr", "merge", str(pr["number"]), "--repo", repo, "--squash",
                    "--match-head-commit", pr["headRefOid"]])
                break
            except ShipError as exc:  # e.g. the PR moved on after all
                waiting = [f"GitHub didn't merge #{pr['number']} ({exc})"]
        if not wait:
            raise ShipError("Not shipping yet: " + "; ".join(waiting))
        _until(deadline, sleep, "; ".join(waiting))
    wait_for_publish(gh, repo, pr["number"], tag, sleep, time.monotonic() + publish_timeout_s)
    problems = verify(gh, repo, tag, workdir, location_of)
    for _ in range(2):  # "Latest" and the download link can lag a moment behind publishing
        if not problems:
            break
        sleep(POLL_S)
        problems = verify(gh, repo, tag, workdir, location_of)
    if problems:
        raise ShipError(f"{tag} is published but: " + "; ".join(problems))
    return tag


def _published(gh, repo: str, tag: str) -> bool:
    try:
        return not _json(gh, ["release", "view", tag, "--repo", repo, "--json", "isDraft"])["isDraft"]
    except NotFound:
        return False  # release-please hasn't made it yet


def wait_for_publish(gh, repo: str, number: int, tag: str, sleep, deadline: float) -> None:
    """After merging: wait for the Release workflow to build, attach the files and publish `tag`."""
    after = f"#{number} is merged; once {tag} is published: python scripts/ship.py verify {tag}"
    merged, errors, finished = None, 0, False
    while True:
        try:
            merged = merged or gh(["api", f"repos/{repo}/pulls/{number}", "--jq", ".merge_commit_sha // empty"]).strip()
            if _published(gh, repo, tag):
                return
            runs = _runs(gh, repo, merged) if merged else []
            errors = 0
        except ShipError as exc:
            errors += 1
            if errors >= GH_RETRIES:
                raise ShipError(f"Checking on the release keeps failing ({exc}). {after}") from exc
            _until(deadline, sleep, f"{tag} to be published ({after})")
            continue
        if broken := [r for r in runs if r["status"] == "completed" and r["conclusion"] in FAILED]:
            raise ShipError(f"The Release run failed, so {tag} is still a draft: {broken[0]['url']}. Use "
                            f"\"Re-run failed jobs\" on it, then: python scripts/ship.py verify {tag}")
        if any(r["status"] == "completed" and r["conclusion"] == "success" for r in runs):
            if finished:  # publishing is the run's last step: one more look, then it's not coming
                raise ShipError(f"The Release run finished but {tag} isn't published: {runs[0]['url']}")
            finished = True
        _until(deadline, sleep, f"{tag} to be published ({after})")


def verify(gh, repo: str, tag: str, workdir: Path, location_of) -> list[str]:
    """What's wrong with the published release `tag`; empty when it's all as it should be."""
    version = check_tag(tag).removeprefix("v")
    try:
        info = _json(gh, ["release", "view", tag, "--repo", repo, "--json", "isDraft,assets"])
    except NotFound:
        return [f"there's no release {tag}"]
    problems = [f"{tag} is still a draft"] if info["isDraft"] else []
    if missing := sorted(expected_assets(version) - {a["name"] for a in info["assets"]}):
        problems.append("missing files: " + ", ".join(missing))
    latest = gh(["api", f"repos/{repo}/releases/latest", "--jq", ".tag_name"]).strip()
    if latest != tag:
        problems.append(f"Latest is {latest}, not {tag}")
    link = location_of(f"{project.repo_url(project.SERVER_URL, repo)}/releases/latest/download/SnipForge-setup.exe")
    if not (link or "").endswith(f"/download/{tag}/SnipForge-setup.exe"):
        problems.append(f"the permanent download link goes to {link or 'nowhere'}")
    installers = [f"SnipForge-{version}-setup.exe", "SnipForge-setup.exe"]
    with tempfile.TemporaryDirectory(dir=workdir) as folder:
        patterns = [arg for name in [*installers, "SHA256SUMS.txt"] for arg in ("--pattern", name)]
        try:
            gh(["release", "download", tag, "--repo", repo, *patterns, "--dir", folder, "--clobber"])
        except ShipError as exc:
            return [*problems, f"couldn't download the installers: {exc}"]
        sums_file = Path(folder) / "SHA256SUMS.txt"
        sums = parse_sums(sums_file.read_text(encoding="utf-8")) if sums_file.exists() else {}
        attested = not _json(gh, ["repo", "view", repo, "--json", "isPrivate"])["isPrivate"]  # free for public repos
        for name in installers:
            path = Path(folder) / name
            if not path.exists():
                problems.append(f"couldn't download {name}")
                continue
            if sums.get(name) != hashlib.sha256(path.read_bytes()).hexdigest():
                problems.append(f"{name}'s checksum doesn't match SHA256SUMS.txt")
            if attested:
                try:
                    gh(["attestation", "verify", str(path), "--repo", repo,
                        "--signer-workflow", f"{repo}/.github/workflows/{RELEASE_WORKFLOW}"])
                except ShipError as exc:
                    problems.append(f"{name}'s build attestation didn't verify: {exc}")
    return problems


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kw):
        return None


def location_of(url: str) -> str | None:
    """Where `url` redirects to, without following it; None if it doesn't (or can't be reached)."""
    if not url.startswith("https://"):
        raise ShipError(f"Only https links are checked, not {url}")
    request = urllib.request.Request(url, method="HEAD")  # noqa: S310 - https only, checked above
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=30):
            return None
    except urllib.error.HTTPError as exc:
        return exc.headers.get("Location") if 300 <= exc.code < 400 else None
    except (urllib.error.URLError, OSError):
        return None


def status(gh, repo: str) -> None:
    pr = release_pr(gh, repo)
    if pr is None:
        draft = newest_draft(gh, repo)
        print(f"No release PR is open, but {draft} is still a draft." if draft else
              "No release PR is open: nothing to ship.")
        return
    print(f"Release PR #{pr['number']}: {pr['title']}")
    failed, waiting, _main = blockers(gh, repo, pr)
    for line in failed:
        print(f"  failed: {line}")
    for line in waiting:
        print(f"  waiting: {line}")
    if not failed and not waiting:
        print("  ready: python scripts/ship.py release")


def main(argv: list[str] | None = None, gh=run_gh, link=location_of) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=project.REPO, help=f"owner/name (default {project.REPO})")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    rel = sub.add_parser("release")
    rel.add_argument("--wait", action="store_true", help="wait for merging PRs, checks and release-please")
    rel.add_argument("--timeout", type=float, default=120, help="minutes to wait before merging (default 120)")
    ver = sub.add_parser("verify")
    ver.add_argument("tag")
    args = parser.parse_args(argv)
    workdir = Path(tempfile.gettempdir())
    try:
        repo = project.check_repo(args.repo)
        if args.command == "status":
            status(gh, repo)
        elif args.command == "verify":
            if problems := verify(gh, repo, args.tag, workdir, link):
                raise ShipError(f"{args.tag}: " + "; ".join(problems))
            print(f"{args.tag} is published, Latest, complete, and its installers check out.")
        else:
            tag = ship(gh, repo, workdir=workdir, location_of=link, wait=args.wait, timeout_s=args.timeout * 60)
            print("No release PR is open: nothing to ship." if tag is None else
                  f"Shipped {tag}: published, Latest, complete, and its installers check out.")
    except (ShipError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
