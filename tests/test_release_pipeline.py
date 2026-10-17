"""Release pipeline: the release PR gets its CI, and a release stays a draft until its files are attached.

release-please creates the release as a draft (with its tag), and release.yml's publish job makes it
public only after the installer is uploaded, so /releases/latest/download/... never points at a
release without its files. The workflow steps' shell scripts run here against a fake `gh`.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE_YML = ROOT / ".github" / "workflows" / "release.yml"
APPROVE_STEP = "Approve CI on the release PR"
PUBLISH_STEP = "Publish the release"
HEAD_SHA = "abc123"
LAGGING_SHA = "old999"  # the PR's head right after release-please pushes, before GitHub catches up
STALE_RUN = "900"  # left on the reused release-please branch by an earlier release PR

# A stand-in for the GitHub CLI. Calls are logged to fd 3 so `$(gh ... 2>&1)` doesn't capture them.
# FAKE_RUNS lists the runs waiting at HEAD_SHA on each `gh run list` call, e.g. "/11 12/13/": none,
# then 11 and 12, then 13, then none.
FAKE_GH = r"""
exec 3>&2
export PATH="/usr/bin:$PATH"
FAKE_STATE=$(mktemp); echo 0 > "$FAKE_STATE"
sleep() { :; }
if ! command -v jq >/dev/null; then  # the runners have jq; this covers `jq -r '.field // empty'`
  jq() { "$FAKE_PYTHON" -c "$FAKE_JQ" "$(echo "${2#.}" | cut -d' ' -f1)"; }
fi
gh() {
  echo "CALL gh $*" >&3
  case "$1 $2" in
    "release view")
      if [ "$FAKE_IS_DRAFT" = fail ]; then echo "HTTP 502: Bad Gateway" >&2; return 1; fi
      echo "$FAKE_IS_DRAFT" ;;
    "release edit") ;;
    "api repos/$GITHUB_REPOSITORY/releases/latest")
      case "$FAKE_LATEST" in
        404) echo '{"message":"Not Found"}gh: Not Found (HTTP 404)' >&2; return 1 ;;
        fail) echo "gh: Server Error (HTTP 500)" >&2; return 1 ;;
        *) echo "notice: something on stderr" >&2; echo "$FAKE_LATEST" ;;
      esac ;;
    "pr view") echo "$FAKE_LAGGING_SHA" ;;
    "api repos/$GITHUB_REPOSITORY/git/ref/heads/"*)
      if [ "$FAKE_REF" = fail ]; then echo "gh: Not Found (HTTP 404)" >&2; return 1; fi
      echo "$FAKE_HEAD_SHA" ;;
    "run list")
      case " $* " in
        *" --commit $FAKE_HEAD_SHA "*)
          n=$(( $(cat "$FAKE_STATE") + 1 )); echo "$n" > "$FAKE_STATE"
          echo "$FAKE_RUNS" | cut -d/ -f"$n" | tr ' ' '\n' | grep . || true ;;
        *) echo "$FAKE_STALE_RUN" ;;
      esac ;;
    "api -X") ;;
    *) echo "unexpected gh call: $*" >&2; return 2 ;;
  esac
}
"""
FAKE_JQ = 'import json, sys; v = json.load(sys.stdin).get(sys.argv[1]); sys.stdout.write("" if v is None else str(v))'


def find_bash():
    found = shutil.which("bash")
    # On Windows, System32\bash.exe is the WSL launcher and WindowsApps\bash.exe a store alias.
    if found and not any(part in found.lower() for part in ("system32", "windowsapps")):
        return found
    git = shutil.which("git")
    for root in Path(git).parents if git else []:
        if (root / "bin" / "bash.exe").is_file():  # Git for Windows: <root>\cmd\git.exe
            return str(root / "bin" / "bash.exe")
    return None


def step_lines(name, workflow=RELEASE_YML):
    lines = workflow.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"      - name: {name}")
    # The step ends at the next step, or at the end of its job (a line indented 6 or fewer).
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) <= 6), len(lines))
    return lines[start:end]


def step_script(name, workflow=RELEASE_YML):
    """The `run: |` block of the workflow step called `name`, dedented."""
    lines = step_lines(name, workflow)
    run = next(i for i in range(len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    body = []
    for line in lines[run + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body).rstrip("\n") + "\n"  # `|` keeps one final newline


def run_step(name, **fake_env):
    bash = find_bash()
    if bash is None:
        # These are the only checks of the workflow steps, so CI must not pass by skipping them.
        (pytest.fail if os.environ.get("CI") else pytest.skip)("needs Git Bash (or bash) to run the workflow step")
    env = {**os.environ, "GITHUB_REPOSITORY": "owner/snipforge", "GH_TOKEN": "fake",
           "FAKE_PYTHON": sys.executable, "FAKE_JQ": FAKE_JQ,
           "FAKE_HEAD_SHA": HEAD_SHA, "FAKE_LAGGING_SHA": LAGGING_SHA, "FAKE_STALE_RUN": STALE_RUN, **fake_env}
    script = FAKE_GH + step_script(name)
    # GitHub's flags for `shell: bash`. Bytes, so Windows doesn't turn the newlines into CRLF.
    proc = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", "-s"], input=script.encode(),
                          capture_output=True, env=env, timeout=60)
    stderr = proc.stderr.decode("utf-8", "replace")
    calls = [line.removeprefix("CALL gh ") for line in stderr.splitlines() if line.startswith("CALL gh ")]
    return proc.returncode, calls, proc.stdout.decode("utf-8", "replace") + stderr


def run_publish(tag, is_draft, latest):
    return run_step(PUBLISH_STEP, TAG=tag, FAKE_IS_DRAFT=is_draft, FAKE_LATEST=latest)


def run_approve(pr_json, runs, **fake_env):
    return run_step(APPROVE_STEP, PR_JSON=pr_json, FAKE_RUNS=runs, **fake_env)


def edits(calls):
    return [c for c in calls if c.startswith("release edit")]


def approved(calls):
    return [c.split("/")[-2] for c in calls if c.startswith("api -X POST")]


# --- configuration -------------------------------------------------------------------------------

def test_release_please_creates_drafts_with_their_tag():
    config = json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    assert config.get("draft") is True
    # GitHub only creates a draft's tag when it's published. Without the tag, release-please can't
    # find the previous release and the next release PR would list the whole history.
    assert config.get("force-tag-creation") is True


def test_publish_job_makes_the_release_public_after_uploading():
    text = RELEASE_YML.read_text(encoding="utf-8")
    publish_job = text[text.index("\n  publish:"):]
    assert publish_job.index("gh release upload") < publish_job.index(f"- name: {PUBLISH_STEP}")
    assert "--draft=false" in step_script(PUBLISH_STEP)


@pytest.mark.parametrize("step", [APPROVE_STEP, PUBLISH_STEP])
def test_steps_run_with_the_flags_the_tests_use(step):
    # The tests run each script with `shell: bash`'s flags (-eo pipefail), not the default -e.
    assert "        shell: bash" in step_lines(step)


# --- approving the release PR's CI ---------------------------------------------------------------

RELEASE_PR = json.dumps({"headBranchName": "release-please--branches--main--components--snipforge", "number": 10})


def test_approves_the_release_prs_current_runs_only():
    # Its workflows can start a few seconds apart, and older runs on the reused branch aren't its own.
    code, calls, out = run_approve(RELEASE_PR, "/11 12/13/")
    assert code == 0, out
    assert approved(calls) == ["11", "12", "13"]


def test_reads_the_new_commit_from_the_branch_not_the_lagging_pr():
    # Seconds after release-please updates an existing release PR, GitHub can still report the PR's
    # previous commit; the branch itself has the new one. 0.8.0's CI sat unapproved because of this.
    code, calls, out = run_approve(RELEASE_PR, "/11/")
    assert code == 0, out
    assert "api repos/owner/snipforge/git/ref/heads/release-please--branches--main--components--snipforge" \
           " --jq .object.sha" in calls
    assert not [c for c in calls if c.startswith("pr view")]
    assert STALE_RUN not in approved(calls)


def test_unreadable_branch_warns_instead_of_failing_the_release_run():
    code, calls, out = run_approve(RELEASE_PR, "/11/", FAKE_REF="fail")
    assert code == 0, out
    assert approved(calls) == []
    assert "::warning::" in out and "Actions tab" in out


def test_release_pr_without_a_branch_name_warns():
    code, calls, out = run_approve(json.dumps({"number": 10}), "/11/")
    assert code == 0, out
    assert approved(calls) == [] and "::warning::" in out


def test_no_release_pr_on_this_run_does_nothing():
    code, calls, out = run_approve("", "/11/")
    assert code == 0, out
    assert calls == []


def test_warns_when_no_runs_are_waiting():
    code, calls, out = run_approve(RELEASE_PR, "")
    assert code == 0, out
    assert approved(calls) == []
    assert "::warning::" in out


# --- the publish step ----------------------------------------------------------------------------

def test_new_draft_is_published_as_latest():
    code, calls, err = run_publish("v0.6.0", "true", "v0.5.0")
    assert code == 0, err
    assert edits(calls) == ["release edit v0.6.0 --repo owner/snipforge --draft=false --latest=true"]


def test_first_stable_release_is_published_as_latest():
    code, calls, err = run_publish("v1.0.0", "true", "404")
    assert code == 0, err
    assert edits(calls) == ["release edit v1.0.0 --repo owner/snipforge --draft=false --latest=true"]


def test_versions_compare_numerically():
    code, calls, err = run_publish("v0.10.0", "true", "v0.9.0")
    assert code == 0, err
    assert edits(calls)[0].endswith("--latest=true")


def test_older_draft_does_not_take_latest_back():
    # e.g. v0.6.0's build failed, v0.6.1 shipped, then v0.6.0 is rebuilt by hand
    code, calls, err = run_publish("v0.6.0", "true", "v0.6.1")
    assert code == 0, err
    assert edits(calls) == ["release edit v0.6.0 --repo owner/snipforge --draft=false --latest=false"]


def test_rebuilding_a_published_release_leaves_it_alone():
    code, calls, err = run_publish("v0.4.0", "false", "v0.5.0")
    assert code == 0, err
    assert edits(calls) == []


@pytest.mark.parametrize(("is_draft", "latest"), [
    ("fail", "v0.5.0"),    # can't tell whether it's a draft
    ("true", "fail"),      # can't read the latest release
    ("true", "nightly"),   # a hand-made release is Latest; can't compare versions
])
def test_lookup_failures_stop_without_publishing(is_draft, latest):
    code, calls, _ = run_publish("v0.6.0", is_draft, latest)
    assert code != 0
    assert edits(calls) == []
