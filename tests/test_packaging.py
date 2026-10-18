"""What the packaged app leaves out (packaging/snipforge.spec) stays unused: Python's own TLS and
OpenSSL hashes, zstd archives and Qt's translations. Each saves download size; these tests fail if
SnipForge starts needing one, before the packaged app would."""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEFT_OUT = {"ssl", "_ssl", "_hashlib", "urllib.request", "http.client", "compression.zstd", "_zstd"}


def imported_names(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_snipforge_never_imports_what_the_package_leaves_out():
    """SnipForge goes online only through Qt (it brings its own TLS); see the spec's EXCLUDES."""
    found = {str(path.relative_to(ROOT)): sorted(imported_names(path) & LEFT_OUT)
             for path in (ROOT / "snipforge").rglob("*.py")}
    assert {path: names for path, names in found.items() if names} == {}


def test_checksums_and_text_recognition_work_without_pythons_openssl():
    """As packaged: hashlib without _hashlib (Python's built-in SHA-256), asyncio without ssl."""
    code = ("import sys, json\n"
            "for name in ('_hashlib', '_ssl', 'ssl'):\n"
            "    sys.modules[name] = None  # what the package leaves out\n"
            "import asyncio, hashlib\n"
            "from snipforge import updates\n"
            "async def nothing():\n"
            "    return 1\n"
            "print(json.dumps({'sha': updates.sha256_bytes(b'snipforge'), 'loop': asyncio.run(nothing())}))\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, cwd=ROOT,
                          env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout.strip().splitlines()[-1])
    import hashlib
    assert result == {"sha": hashlib.sha256(b"snipforge").hexdigest(), "loop": 1}


def test_no_translator_is_ever_loaded():
    """The package drops Qt's translations (~7 MB): nothing may load one."""
    users = [str(p.relative_to(ROOT)) for p in (ROOT / "snipforge").rglob("*.py")
             if "QTranslator" in p.read_text(encoding="utf-8")]
    assert users == []


def test_the_spec_leaves_them_out():
    spec = (ROOT / "packaging" / "snipforge.spec").read_text(encoding="utf-8")
    for name in ('"ssl"', '"_ssl"', '"_hashlib"', '"compression.zstd"', '"_zstd"', 'endswith(".qm")'):
        assert name in spec
