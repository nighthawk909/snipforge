"""Authenticode signing with Azure Artifact Signing, for `python scripts/build.py --sign`.

signtool.exe signs through the Artifact Signing "dlib", which signs in to Azure with the Azure CLI
(`az login` on your PC, the azure/login step in CI) and reads the signing account and certificate
profile from a metadata.json. build.py finds all three through environment variables:

    SIGNTOOL                   signtool.exe from the Windows SDK build tools
    ARTIFACT_SIGNING_DLIB      Azure.CodeSigning.Dlib.dll (x64)
    ARTIFACT_SIGNING_METADATA  metadata.json

`setup` downloads pinned, checksum-checked copies of signtool and the dlib, writes metadata.json
and prints those variables as PowerShell lines to paste (in CI, --github-env also adds them to
$GITHUB_ENV for the later steps):

    python scripts/codesign.py setup --dest $env:LOCALAPPDATA\\SnipForgeSigning `
        --endpoint https://eus.codesigning.azure.net --account <account> --profile <profile>

The Azure side (account, identity validation, certificate profile, GitHub login) is in
docs/CODE-SIGNING.md. The dlib needs the .NET 8 runtime or newer.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

TIMESTAMP_URL = "http://timestamp.acs.microsoft.com"  # RFC 3161; the responses are themselves signed
ENV_VARS = ("SIGNTOOL", "ARTIFACT_SIGNING_DLIB", "ARTIFACT_SIGNING_METADATA")
INNO_SIGN_TOOL = "snipforge"  # the SignTool name packaging/installer.iss uses
ENDPOINT_RE = re.compile(r"https://[a-z0-9]+\.codesigning\.azure\.net/?")
NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,98}[A-Za-z0-9])?")
# Every login the dlib could try except the Azure CLI: no waiting on ones that can't work on a build
# runner, and no browser pop-ups.
EXCLUDED_CREDENTIALS = (
    "EnvironmentCredential", "WorkloadIdentityCredential", "ManagedIdentityCredential",
    "SharedTokenCacheCredential", "VisualStudioCredential", "VisualStudioCodeCredential",
    "AzurePowerShellCredential", "AzureDeveloperCliCredential", "InteractiveBrowserCredential",
)


@dataclass(frozen=True)
class Tool:
    """A NuGet package pinned by version and SHA-256, and the folder in it that build.py needs."""
    package: str
    version: str
    sha256: str
    folder: str

    @property
    def url(self) -> str:
        return f"https://www.nuget.org/api/v2/package/{self.package}/{self.version}"


# The versions Microsoft's own artifact-signing-action v2.0.0 uses together. The SHA-256s were taken
# from downloads whose SHA-512 matched nuget.org's catalog.
SIGNTOOL_PACKAGE = Tool("Microsoft.Windows.SDK.BuildTools", "10.0.26100.4188",
                        "180deb372659029864c10a0c04787833234d64aacd1d2c0661d2c00295d8e022",
                        "bin/10.0.26100.0/x64/")
DLIB_PACKAGE = Tool("Microsoft.ArtifactSigning.Client", "1.0.128",
                    "74bd7d27e6ce1051409c38d9b46bc8df0400ecd643d51ffbf2ac00869061e40b",
                    "bin/x64/")
TOOLS = (SIGNTOOL_PACKAGE, DLIB_PACKAGE)


def fetch_url(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=300) as response:  # noqa: S310 - fixed https nuget.org URLs
        return response.read()


def install(tool: Tool, dest: Path, fetch: Callable[[str], bytes] = fetch_url) -> Path:
    """Download `tool`, check it against its pin, and extract its folder into dest. Returns that folder."""
    data = fetch(tool.url)
    digest = hashlib.sha256(data).hexdigest()
    if digest != tool.sha256:
        sys.exit(f"{tool.package} {tool.version} doesn't match its pinned checksum (got {digest}); not using it.")
    out = (dest / f"{tool.package}.{tool.version}").resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        members = [m for m in package.infolist() if m.filename.startswith(tool.folder) and not m.is_dir()]
        if not members:
            sys.exit(f"{tool.package} {tool.version} has nothing in {tool.folder}")
        for member in members:
            target = (out / member.filename[len(tool.folder):]).resolve()
            if not target.is_relative_to(out):
                sys.exit(f"{tool.package} has a file outside its folder: {member.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(package.read(member))
    return out


def write_metadata(path: Path, endpoint: str, account: str, profile: str) -> Path:
    """The dlib's metadata.json: which Artifact Signing account and certificate profile to sign with."""
    if not ENDPOINT_RE.fullmatch(endpoint):
        sys.exit(f"The endpoint must look like https://eus.codesigning.azure.net, got {endpoint!r}")
    for label, value in (("account", account), ("certificate profile", profile)):
        if not NAME_RE.fullmatch(value):
            sys.exit(f"The {label} name must be letters, digits and hyphens, got {value!r}")
    metadata = {"Endpoint": endpoint.rstrip("/"), "CodeSigningAccountName": account,
                "CertificateProfileName": profile, "ExcludeCredentials": list(EXCLUDED_CREDENTIALS)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return path


@dataclass(frozen=True)
class Signer:
    signtool: Path
    dlib: Path
    metadata: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Signer:
        missing = [name for name in ENV_VARS if not env.get(name)]
        if missing:
            sys.exit(f"--sign needs {', '.join(missing)}: run scripts/codesign.py setup (docs/CODE-SIGNING.md)")
        signer = cls(*(Path(env[name]) for name in ENV_VARS))
        for name, path in zip(ENV_VARS, (signer.signtool, signer.dlib, signer.metadata), strict=True):
            if not path.is_file():
                sys.exit(f"{name} points at {path}, which doesn't exist")
        return signer

    def sign_command(self) -> list[str]:
        return [str(self.signtool), "sign", "/v", "/fd", "SHA256", "/tr", TIMESTAMP_URL, "/td", "SHA256",
                "/dlib", str(self.dlib), "/dmdf", str(self.metadata)]

    def sign(self, files: Iterable[Path], run: Callable[[list[str]], None]) -> None:
        run([*self.sign_command(), *(str(f) for f in files)])

    def verify(self, files: Iterable[Path], run: Callable[[list[str]], None]) -> None:
        """Fails unless each file has a valid signature that chains to a trusted root."""
        run([str(self.signtool), "verify", "/pa", "/v", *(str(f) for f in files)])

    def inno_command(self) -> str:
        """The command Inno Setup runs to sign Setup.exe and its uninstaller: $q is a quote and $f the
        file. With no literal quotes, the whole /S definition passes to ISCC as one argument."""
        parts = self.sign_command()
        if any("$" in part for part in parts):
            sys.exit("Inno Setup can't take signing paths that contain $; move the signing tools")
        return " ".join(f"$q{part}$q" for part in parts) + " $f"


def setup(dest: Path, endpoint: str, account: str, profile: str, fetch: Callable[[str], bytes] = fetch_url
          ) -> dict[str, str]:
    """Install the signing tools and metadata.json under dest; returns the variables build.py reads."""
    metadata = write_metadata(dest / "metadata.json", endpoint, account, profile)
    signtool = install(SIGNTOOL_PACKAGE, dest, fetch) / "signtool.exe"
    dlib = install(DLIB_PACKAGE, dest, fetch) / "Azure.CodeSigning.Dlib.dll"
    for path in (signtool, dlib):
        if not path.is_file():
            sys.exit(f"{path.name} wasn't in its package")
    return dict(zip(ENV_VARS, (str(signtool), str(dlib), str(metadata)), strict=True))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("setup", help="install the signing tools and write metadata.json")
    cmd.add_argument("--dest", type=Path, required=True, help="folder for the tools and metadata.json")
    cmd.add_argument("--endpoint", required=True, help="the signing account's endpoint, e.g. https://eus.codesigning.azure.net")
    cmd.add_argument("--account", required=True, help="Artifact Signing account name")
    cmd.add_argument("--profile", required=True, help="certificate profile name")
    cmd.add_argument("--github-env", action="store_true", help="also add the variables to $GITHUB_ENV (CI)")
    args = parser.parse_args(argv)
    github_env = os.environ.get("GITHUB_ENV") if args.github_env else None
    if args.github_env and not github_env:
        sys.exit("--github-env only works in GitHub Actions ($GITHUB_ENV isn't set)")
    variables = setup(args.dest, args.endpoint, args.account, args.profile)
    if github_env:
        with open(github_env, "a", encoding="utf-8") as env_file:
            env_file.write("".join(f"{name}={value}\n" for name, value in variables.items()))
    print(powershell_lines(variables))


def powershell_lines(variables: Mapping[str, str]) -> str:
    """`$env:NAME = 'value'` lines to paste into PowerShell before `build.py --sign`."""
    return "\n".join(f"$env:{name} = '{value.replace(chr(39), chr(39) * 2)}'" for name, value in variables.items())


if __name__ == "__main__":
    main()
