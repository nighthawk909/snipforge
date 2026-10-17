"""Code signing: scripts/codesign.py, `scripts/build.py --sign`, and the workflows that use them.

Release and preview builds sign SnipForge.exe, the installer and its uninstaller with Azure Artifact
Signing once the repository has it set up (docs/CODE-SIGNING.md). Until then they build unsigned.
"""
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tests.test_release_pipeline import find_bash, step_lines, step_script

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
BUILD_YML = WORKFLOWS / "build.yml"
SETUP_STEP = "Code signing setup"
SIGNING_VARS = ("ARTIFACT_SIGNING_ENDPOINT", "ARTIFACT_SIGNING_ACCOUNT", "ARTIFACT_SIGNING_PROFILE",
                "AZURE_CLIENT_ID", "AZURE_TENANT_ID")
ENDPOINT = "https://eus.codesigning.azure.net"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build = _load("build")
codesign = build.codesign


def make_package(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buffer.getvalue()


def tool_for(data, folder="bin/x64/"):
    return codesign.Tool("Example.Package", "1.0.0", hashlib.sha256(data).hexdigest(), folder)


@pytest.fixture
def signer(tmp_path):
    folder = tmp_path / "signing tools"  # a space, like C:\Program Files
    folder.mkdir()
    paths = [folder / "signtool.exe", folder / "Azure.CodeSigning.Dlib.dll", folder / "metadata.json"]
    paths[0].write_bytes(b"")
    paths[1].write_bytes(b"")
    codesign.write_metadata(paths[2], ENDPOINT, "snipforge", "snipforge-public")
    return codesign.Signer(*paths)


# --- the signing tools ----------------------------------------------------------------------------

def test_pinned_tools_come_from_nuget_with_a_sha256():
    for tool in codesign.TOOLS:
        assert tool.url.startswith("https://www.nuget.org/api/v2/package/")
        assert re.fullmatch(r"[0-9a-f]{64}", tool.sha256)
        assert tool.folder.endswith("/")


def test_install_extracts_only_the_tools_folder(tmp_path):
    data = make_package({"bin/x64/Azure.CodeSigning.Dlib.dll": b"dlib", "bin/x64/sub/dep.dll": b"dep",
                         "bin/x86/Azure.CodeSigning.Dlib.dll": b"wrong arch", "readme.txt": b"no"})
    out = codesign.install(tool_for(data), tmp_path, fetch=lambda url: data)
    assert (out / "Azure.CodeSigning.Dlib.dll").read_bytes() == b"dlib"
    assert (out / "sub" / "dep.dll").read_bytes() == b"dep"
    assert sorted(p.name for p in out.rglob("*") if p.is_file()) == ["Azure.CodeSigning.Dlib.dll", "dep.dll"]


def test_install_refuses_a_package_that_doesnt_match_its_pin(tmp_path):
    data = make_package({"bin/x64/Azure.CodeSigning.Dlib.dll": b"dlib"})
    tampered = make_package({"bin/x64/Azure.CodeSigning.Dlib.dll": b"evil"})
    with pytest.raises(SystemExit, match="checksum"):
        codesign.install(tool_for(data), tmp_path, fetch=lambda url: tampered)
    assert not any(tmp_path.iterdir())


def test_install_refuses_paths_that_leave_the_tools_folder(tmp_path):
    data = make_package({"bin/x64/../../../evil.dll": b"evil"})
    with pytest.raises(SystemExit, match="outside"):
        codesign.install(tool_for(data), tmp_path / "tools", fetch=lambda url: data)
    assert not (tmp_path / "evil.dll").exists()


def test_install_fails_when_the_package_has_no_tools_folder(tmp_path):
    data = make_package({"lib/other.dll": b"x"})
    with pytest.raises(SystemExit, match="bin/x64/"):
        codesign.install(tool_for(data), tmp_path, fetch=lambda url: data)


# --- metadata.json --------------------------------------------------------------------------------

def test_metadata_names_the_account_and_signs_in_with_the_azure_cli_only(tmp_path):
    path = codesign.write_metadata(tmp_path / "metadata.json", ENDPOINT + "/", "snipforge", "snipforge-public")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["Endpoint"] == ENDPOINT
    assert data["CodeSigningAccountName"] == "snipforge"
    assert data["CertificateProfileName"] == "snipforge-public"
    assert "AzureCliCredential" not in data["ExcludeCredentials"]
    # No waiting on credentials that can't work on a build runner, and no browser pop-ups.
    assert {"ManagedIdentityCredential", "InteractiveBrowserCredential"} <= set(data["ExcludeCredentials"])


@pytest.mark.parametrize("endpoint, account, profile", [
    ("http://eus.codesigning.azure.net", "snipforge", "public"),
    ("https://eus.codesigning.azure.net.evil.example", "snipforge", "public"),
    ("https://evil.example/eus.codesigning.azure.net", "snipforge", "public"),
    (ENDPOINT, "snip forge", "public"),
    (ENDPOINT, "snipforge", 'public" /dlib x'),
    (ENDPOINT, "", "public"),
])
def test_metadata_rejects_unexpected_values(tmp_path, endpoint, account, profile):
    with pytest.raises(SystemExit):
        codesign.write_metadata(tmp_path / "metadata.json", endpoint, account, profile)
    assert not (tmp_path / "metadata.json").exists()


# --- signing --------------------------------------------------------------------------------------

def test_signer_from_env_says_what_is_missing():
    with pytest.raises(SystemExit) as raised:
        codesign.Signer.from_env({})
    message = str(raised.value)
    assert all(name in message for name in codesign.ENV_VARS) and "CODE-SIGNING.md" in message


def test_signer_from_env_checks_the_files_exist(signer, tmp_path):
    env = dict(zip(codesign.ENV_VARS, map(str, (signer.signtool, signer.dlib, signer.metadata))))
    assert codesign.Signer.from_env(env) == signer
    with pytest.raises(SystemExit, match="ARTIFACT_SIGNING_DLIB"):
        codesign.Signer.from_env({**env, "ARTIFACT_SIGNING_DLIB": str(tmp_path / "missing.dll")})


def test_signatures_use_sha256_and_a_timestamp(signer, tmp_path):
    calls = []
    signer.sign([tmp_path / "SnipForge.exe"], calls.append)
    cmd = calls[0]
    assert cmd[:2] == [str(signer.signtool), "sign"]
    # Artifact Signing certificates last three days: the timestamp keeps signatures valid after that.
    assert cmd[cmd.index("/tr") + 1] == codesign.TIMESTAMP_URL
    assert cmd[cmd.index("/fd") + 1] == "SHA256" and cmd[cmd.index("/td") + 1] == "SHA256"
    assert cmd[cmd.index("/dlib") + 1] == str(signer.dlib)
    assert cmd[cmd.index("/dmdf") + 1] == str(signer.metadata)
    assert cmd[-1] == str(tmp_path / "SnipForge.exe")


def test_verify_checks_the_signature_chain(signer, tmp_path):
    calls = []
    signer.verify([tmp_path / "a.exe", tmp_path / "b.exe"], calls.append)
    assert calls[0][:3] == [str(signer.signtool), "verify", "/pa"]
    assert calls[0][-2:] == [str(tmp_path / "a.exe"), str(tmp_path / "b.exe")]


def test_inno_sign_command_quotes_paths_for_inno(signer):
    command = signer.inno_command()
    assert command.endswith(" $f")  # Inno Setup puts the (quoted) file to sign here
    assert '"' not in command  # quotes are $q, so the whole command survives as one ISCC argument
    assert f"$q{signer.signtool}$q" in command and f"$q{signer.metadata}$q" in command


def test_inno_sign_command_refuses_dollar_signs(tmp_path):
    folder = tmp_path / "$tools"
    folder.mkdir()
    paths = [folder / "signtool.exe", folder / "dlib.dll", folder / "metadata.json"]
    with pytest.raises(SystemExit, match=r"\$"):
        codesign.Signer(*paths).inno_command()


# --- build.py --sign -----------------------------------------------------------------------------

@pytest.fixture
def fake_build(tmp_path, monkeypatch):
    """build.py with PyInstaller, zip and Inno Setup replaced: records what happens, in order."""
    events = []
    app_dir = tmp_path / "dist" / "SnipForge"

    def build_exe(version):
        app_dir.mkdir(parents=True)
        (app_dir / "SnipForge.exe").write_bytes(b"app")
        events.append("pyinstaller")
        return app_dir

    def run(cmd):
        if cmd[0] == "ISCC.exe":
            events.append(("iscc", cmd))
            (tmp_path / "dist" / "SnipForge-0.9.0-setup.exe").write_bytes(b"setup")
        else:
            events.append((cmd[1], [Path(c).name for c in cmd[2:] if c.endswith(".exe")]))

    monkeypatch.setattr(build, "DIST", tmp_path / "dist")
    monkeypatch.setattr(build, "build_exe", build_exe)
    monkeypatch.setattr(build, "build_zip", lambda d, v: events.append("zip") or tmp_path / "dist" / "p.zip")
    monkeypatch.setattr(build, "find_iscc", lambda: Path("ISCC.exe"))
    monkeypatch.setattr(build, "run", run)
    return events


def test_signed_build_signs_the_app_before_packaging_it(fake_build, signer):
    outputs = build.build_all("0.9.0", installer=True, signer=signer)

    names = [e if isinstance(e, str) else e[0] for e in fake_build]
    assert names == ["pyinstaller", "sign", "zip", "iscc", "verify"]
    assert fake_build[1] == ("sign", ["SnipForge.exe"])  # so the zip and the installer carry the signed app
    iscc = fake_build[3][1]
    assert "/DSign" in iscc and f"/Ssnipforge={signer.inno_command()}" in iscc  # setup.exe and uninstaller
    assert fake_build[4] == ("verify", ["SnipForge.exe", "SnipForge-0.9.0-setup.exe"])
    assert [p.name for p in outputs] == ["p.zip", "SnipForge-0.9.0-setup.exe", "SnipForge-setup.exe"]


def test_unsigned_build_gives_inno_no_signing_options(fake_build):
    build.build_all("0.9.0", installer=True)
    iscc = next(e[1] for e in fake_build if e[0] == "iscc")
    assert not [a for a in iscc if a.startswith(("/DSign", "/S"))]
    assert "sign" not in [e[0] for e in fake_build if isinstance(e, tuple)]


def test_sign_option_stops_before_building_when_signing_isnt_set_up(monkeypatch):
    for name in codesign.ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["build.py", "--installer", "--sign"])
    monkeypatch.setattr(build, "build_all", lambda *a, **k: pytest.fail("built without signing tools"))
    with pytest.raises(SystemExit, match="SIGNTOOL"):
        build.main()


def test_installer_signs_its_uninstaller_when_signing():
    text = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    block = text[text.index("#ifdef Sign"):text.index("#endif", text.index("#ifdef Sign"))]
    assert f"SignTool={codesign.INNO_SIGN_TOOL}" in block and "SignedUninstaller=yes" in block


def test_inno_setup_runs_the_sign_command_for_the_uninstaller_and_setup(tmp_path, monkeypatch):
    """The real installer.iss and ISCC, with a stand-in signtool that logs what it's asked to sign."""
    iscc = next((c for c in build.ISCC_CANDIDATES if c.exists()), None)
    if iscc is None:
        (pytest.fail if os.environ.get("CI") else pytest.skip)("needs Inno Setup 6 (the CI runners have it)")
    tools = tmp_path / "signing tools"
    tools.mkdir()
    log = tmp_path / "signed.log"
    fake_signtool = tools / "signtool.cmd"
    fake_signtool.write_text(f'@echo %* >> "{log}"\r\n@exit /b 0\r\n', encoding="utf-8")
    signer = codesign.Signer(fake_signtool, tools / "Azure.CodeSigning.Dlib.dll", tools / "metadata.json")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "SnipForge.exe").write_bytes(b"MZ stand-in")
    monkeypatch.setattr(build, "DIST", tmp_path / "out")
    monkeypatch.setattr(build, "find_iscc", lambda: iscc)

    setup = build.build_installer(app_dir, "0.0.1", signer)

    assert setup.is_file()
    calls = log.read_text(encoding="utf-8", errors="replace").splitlines()
    assert len(calls) == 2, calls  # the uninstaller it packs, then Setup.exe itself
    for call in calls:
        assert call.startswith(f"sign /v /fd SHA256 /tr {codesign.TIMESTAMP_URL} /td SHA256 /dlib ")
    assert setup.name in calls[-1]


# --- the workflows -------------------------------------------------------------------------------

def job_block(workflow, job):
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    start = text.index(f"\n  {job}:\n")
    end = re.search(r"\n  [A-Za-z_-]+:\n", text[start + 1:])
    return text[start:start + 1 + end.start()] if end else text[start:]


@pytest.mark.parametrize("workflow, signs", [("release.yml", True), ("preview.yml", True), ("ci.yml", False)])
def test_only_release_and_preview_builds_sign(workflow, signs):
    block = job_block(workflow, "build")
    assert "uses: ./.github/workflows/build.yml" in block
    assert ("sign: true" in block) is signs
    # build.yml asks for an OIDC token (Azure login), so every caller has to allow it.
    assert "id-token: write" in block


def test_build_workflow_signs_only_when_asked():
    text = BUILD_YML.read_text(encoding="utf-8")
    for trigger in ("workflow_call:", "workflow_dispatch:"):  # dispatch: try signing without a release
        inputs = text[text.index(trigger):]
        sign_input = inputs[inputs.index("      sign:"):inputs.index("default:", inputs.index("      sign:")) + 20]
        assert "type: boolean" in sign_input and "default: false" in sign_input
    assert "if: ${{ inputs.sign }}" in [line.strip() for line in step_lines(SETUP_STEP, BUILD_YML)]
    login = next(line for line in text.splitlines() if "uses: azure/login@" in line)
    assert re.search(r"azure/login@[0-9a-f]{40} # v\d", login)  # pinned to a commit
    # The app's only Azure role is signing: it can't list subscriptions, and signing doesn't need one.
    assert "allow-no-subscriptions: true" in text and "subscription-id" not in text
    enabled = "steps.signing.outputs.enabled == 'true'"
    assert text.count(enabled) >= 3  # Azure login, the signature check, and --sign on the build
    assert "--sign" in step_script("Build", BUILD_YML)


def run_setup_step(tmp_path, **variables):
    """Run build.yml's setup step with GitHub's `shell: bash` flags; `python` only records its arguments."""
    bash = find_bash()
    if bash is None:
        (pytest.fail if os.environ.get("CI") else pytest.skip)("needs Git Bash (or bash) to run the workflow step")
    out_file = tmp_path / "github_output"
    out_file.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in SIGNING_VARS}
    env.update(variables, GITHUB_OUTPUT=str(out_file), RUNNER_TEMP=str(tmp_path))
    script = 'python() { echo "CALL python $*" >&2; }\n' + step_script(SETUP_STEP, BUILD_YML)
    proc = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", "-s"], input=script.encode(),
                          capture_output=True, env=env, timeout=60)
    log = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    return proc.returncode, out_file.read_text(encoding="utf-8"), log


def test_setup_step_builds_unsigned_until_signing_is_set_up(tmp_path):
    code, output, log = run_setup_step(tmp_path)
    assert code == 0, log
    assert "enabled=false" in output
    assert "::warning::" in log and "CODE-SIGNING.md" in log
    assert "CALL python" not in log


def test_setup_step_fails_when_signing_is_partly_set_up(tmp_path):
    code, output, log = run_setup_step(tmp_path, ARTIFACT_SIGNING_ENDPOINT=ENDPOINT, AZURE_CLIENT_ID="id")
    assert code != 0
    assert "::error::" in log and "ARTIFACT_SIGNING_ACCOUNT" in log and "AZURE_TENANT_ID" in log
    assert "enabled=" not in output


def test_setup_step_installs_the_tools_once_signing_is_set_up(tmp_path):
    values = dict(zip(SIGNING_VARS, (ENDPOINT, "snipforge", "public", "client", "tenant"), strict=True))
    code, output, log = run_setup_step(tmp_path, **values)
    assert code == 0, log
    assert "enabled=true" in output
    call = next(line for line in log.splitlines() if line.startswith("CALL python"))
    assert "scripts/codesign.py setup" in call and "--github-env" in call
    assert f"--endpoint {ENDPOINT}" in call and "--account snipforge" in call and "--profile public" in call
