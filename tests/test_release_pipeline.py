"""Release pipeline: a stable release stays a draft until its files are attached.

release-please creates the release as a draft (with its tag), and release.yml's publish job makes it
public only after the installer is uploaded, so /releases/latest/download/... never points at a
release without its files. The publish step's shell script runs here against a fake `gh`.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE_YML = ROOT / ".github" / "workflows" / "release.yml"
PUBLISH_STEP = "Publish the release"

# A stand-in for the GitHub CLI. Calls are logged to fd 3 so `$(gh ... 2>&1)` doesn't capture them.
FAKE_GH = r"""
exec 3>&2
export PATH="/usr/bin:$PATH"
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
    *) echo "unexpected gh call: $*" >&2; return 2 ;;
  esac
}
"""


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


def step_lines(name):
    lines = RELEASE_YML.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"      - name: {name}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("      - ")), len(lines))
    return lines[start:end]


def step_script(name):
    """The `run: |` block of the release.yml step called `name`, dedented."""
    lines = step_lines(name)
    run = next(i for i in range(len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    body = []
    for line in lines[run + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:])
    return "\n".join(body) + "\n"


def run_publish(tag, is_draft, latest):
    bash = find_bash()
    if bash is None:
        # This is the only check of the publish step, so CI must not pass by skipping it.
        (pytest.fail if os.environ.get("CI") else pytest.skip)("needs Git Bash (or bash) to run the workflow step")
    env = {**os.environ, "TAG": tag, "GITHUB_REPOSITORY": "owner/snipforge", "GH_TOKEN": "fake",
           "FAKE_IS_DRAFT": is_draft, "FAKE_LATEST": latest}
    script = FAKE_GH + step_script(PUBLISH_STEP)
    # GitHub's flags for `shell: bash`. Bytes, so Windows doesn't turn the newlines into CRLF.
    proc = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", "-s"], input=script.encode(),
                          capture_output=True, env=env, timeout=60)
    stderr = proc.stderr.decode("utf-8", "replace")
    calls = [line.removeprefix("CALL gh ") for line in stderr.splitlines() if line.startswith("CALL gh ")]
    return proc.returncode, calls, stderr


def edits(calls):
    return [c for c in calls if c.startswith("release edit")]


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
    # The tests below run the script with `shell: bash`'s flags (-eo pipefail), not the default -e.
    assert "        shell: bash" in step_lines(PUBLISH_STEP)


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
