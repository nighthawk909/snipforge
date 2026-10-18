"""Report a problem: what SnipForge puts together for a bug report, and the zip it's saved in.

In it: recent logs, the versions of SnipForge, Python, Qt and the operating system, the display
setup (monitors and their scaling), settings with private values left out, IT policy for updates
(without the feed's location) and the last crash, if there was one.

Never in it: snips or recordings (the Library's folders are counted, nothing in them is read),
window titles, the person's name, computer name, domain or OneDrive folder in file paths, email
addresses, or the names of company servers. Building it sends nothing anywhere: the person reads
it, saves it and decides where it goes (docs/IT-REVIEW.md).
"""
from __future__ import annotations

import datetime as dt
import locale
import os
import platform
import re
import socket
import struct
import sys
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from . import __version__, logs, project, update_platform
from .settings import app_settings

LOG_TAIL_BYTES = 400_000  # the newest end of each process's logs
LEFT_OUT = "(set; left out of reports)"
# Settings whose values are personal, or say where the person's files or screens are.
PRIVATE_KEYS = frozenset({"teams_recent", "output/folder", "capture/last_area", "update_ready",
                          "video/microphone_id", "video/webcam_id"})
SAFE_VALUE = re.compile(r"[\w.+\-# ]{0,40}")  # short codes, numbers, versions, shortcuts, colors
KEEP_HOSTS = frozenset({"github.com", "api.github.com", *project.DOWNLOAD_HOSTS})
# Endings of server names taken out wherever they appear (logger names like snipforge.app never end so).
HOST_ENDINGS = "com|net|org|local|lan|corp|internal|intranet|intra|biz|us|uk|de|fr|nl|ca|au|eu|ch|gov|edu|mil"
GENERIC_LABELS = frozenset({"com", "net", "org", "local", "lan", "corp", "internal", "ad", "co", "www"})
NEVER_INCLUDED = ("your snips and recordings, window titles, your name, your computer's name, email addresses, or "
                  "the names of your organization's servers and network")


@dataclass(frozen=True)
class ReportFile:
    name: str   # its name in the zip
    title: str  # what Report a problem calls it
    text: str


@dataclass(frozen=True)
class Report:
    created: dt.datetime
    files: tuple[ReportFile, ...]

    @property
    def default_name(self) -> str:
        return f"SnipForge-report-{self.created:%Y-%m-%d-%H%M}.zip"

    def file(self, name: str) -> ReportFile | None:
        return next((f for f in self.files if f.name == name), None)


class Scrubber:
    """Takes the person's name, computer, organization and servers out of text.

    `hosts`: server names known to belong to the organization (the update feed's, from policy);
    `hostnames`: this computer's names (by default what the operating system says)."""

    def __init__(self, environ: Mapping[str, str] | None = None, home: Path | None = None,
                 hosts: Sequence[str] = (), hostnames: Sequence[str] | None = None) -> None:
        env = os.environ if environ is None else environ
        home = Path.home() if home is None else Path(home)
        folders = [(env.get(var), "%OneDrive%") for var in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive")]
        folders.append((str(home), "%USERPROFILE%"))
        self._rules: list[tuple[re.Pattern, object]] = []
        for path, replacement in sorted((f for f in folders if f[0]), key=lambda f: -len(f[0])):
            self._rules.append((_path_pattern(path), replacement))
        self._rules += [
            (re.compile(r"(?i)(?<=[\\/])OneDrive - [^\\/\r\n\"'<>|:*?]+"), "OneDrive - <organization>"),
            (re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<email>"),  # from a word's start only
            (re.compile(r"(?i)[\\/]{2,4}\?[\\/]+UNC[\\/]+[^\\/\s'\"<>|]+[\\/]+[^\\/\s'\"<>|]+"),
             r"\\\\<server>\\<share>"),
            # \\server\share however it's written (\\, // or doubled in Python's repr), not \\.\pipe or \\?\,
            # and not the doubled separators after %USERPROFILE% or a placeholder
            (re.compile(r"(?<![\w:%>\\/])[\\/]{2,4}(?![.?][\\/])(?!<)[^\\/\s'\"<>|]+[\\/]+[^\\/\s'\"<>|]+"),
             r"\\\\<server>\\<share>"),
            # file://server/share and file:////server/share (not file:///C:/ or a local file:///path)
            (re.compile(r"(?i)\bfile:(?://(?!/)|/{4,5})(?![a-z]:|[%<])[^/\s'\"<>|]+/[^/\s'\"<>|]+"),
             "file://<server>/<share>"),
            (re.compile(r"(?i)\b([A-Z]:[\\/]+Users[\\/]+)[^\\/:*?\"<>|\r\n]+"), r"\1<user>"),  # anyone's profile
            (re.compile(r"(?<![\w.])/(Users|home)/[^/\s]+"), r"/\1/<user>"),
            (re.compile(r"(?i)\b((?:https?|ftp|file)://)([^/\s:?#]+)"), _host),
            (re.compile(rf"(?i)(?<![\w.-])(?:[a-z0-9][a-z0-9-]*+\.)++(?:{HOST_ENDINGS})\b"), _bare_host),
        ]
        machine = list(hostnames) if hostnames is not None else [platform.node(), socket.gethostname()]
        machine.append(env.get("COMPUTERNAME", ""))
        domain = env.get("USERDNSDOMAIN", "")
        labels: dict[str, str] = {}
        for name in (env.get(var, "") for var in ("USERNAME", "USER", "LOGNAME")):
            labels[name] = "<user>"
        labels[home.name] = "<user>"
        for host in hosts:  # whole names only: "updates" or "10" alone are ordinary words
            labels[host] = "<host>"
        for name in (domain, env.get("USERDOMAIN", ""), *_labels(domain)):
            labels[name] = "<domain>"
        for name in machine:
            labels[name] = "<computer>"
            short = name.split(".", 1)[0]
            if len(short) >= 4 and not short.isdigit():  # not part of an address like 10.1.2.3
                labels[short] = "<computer>"
        for name in sorted((n for n in labels if len(n) >= 2), key=len, reverse=True):
            pattern = re.compile(rf"(?i)(?<![A-Za-z0-9<]){re.escape(name)}(?![A-Za-z0-9>])")
            self._rules.append((pattern, labels[name]))

    def __call__(self, text: str) -> str:
        for pattern, replacement in self._rules:
            text = pattern.sub(replacement, text)
        return text


def _labels(domain: str) -> list[str]:
    """The parts of a DNS domain that name the organization ("contoso" in corp.contoso.com)."""
    return [label for label in domain.split(".") if label and label.lower() not in GENERIC_LABELS]


def _path_pattern(path: str) -> re.Pattern:
    """A folder however it's written: either slash (or doubled, as Python shows them), any case, and
    only as a whole name."""
    parts = [re.escape(part) for part in re.split(r"[\\/]+", path.rstrip("\\/")) if part]
    lead = r"[\\/]+" if path[:1] in ("/", "\\") else ""
    return re.compile(f"(?i){lead}" + r"[\\/]+".join(parts) + r"(?![\w-])")


def _host(match: re.Match) -> str:
    host = match.group(2)
    if host.lower() in KEEP_HOSTS or host.startswith("<"):
        return match.group(0)
    return f"{match.group(1)}<host>"


def _bare_host(match: re.Match) -> str:
    return match.group(0) if match.group(0).lower() in KEEP_HOSTS else "<host>"


def policy_hosts(hives: Sequence[Mapping[str, object]]) -> list[str]:
    """The servers IT policy names (the update feed's), so they're taken out wherever they appear."""
    hosts = []
    for hive in hives:
        feed = str(hive.get("UpdateFeed") or "").strip().strip('"')
        if feed.lower().startswith("https://"):
            host = urlparse(feed).hostname
        elif feed.startswith(("\\\\", "//")):
            host = re.split(r"[\\/]+", feed.lstrip("\\/"))[0]
        else:
            host = None
        if host:
            hosts.append(host)
    return hosts


# ---------------------------------------------------------------------------------- the parts

def summary_text(now: dt.datetime, library_root: Path | None = None, log_folder: Path | None = None) -> str:
    lines = [
        f"SnipForge problem report, made {now:%Y-%m-%d %H:%M} (this PC's time).",
        "Nothing in it has been sent anywhere. Never in it: " + NEVER_INCLUDED + ".",
        "",
        f"SnipForge {__version__} ({_install_kind()})",
        f"Python {platform.python_version()} ({platform.python_implementation()}, {struct.calcsize('P') * 8}-bit)",
        _qt_line(),
        f"Operating system: {operating_system()} on {platform.machine() or 'an unknown processor'}",
        f"Language: {_language()}",
    ]
    if library_root is not None:
        lines.append(f"Library: {_count_folders(library_root)} in {library_root}")
    place = log_folder or logs.folder()
    for name in logs.names(place):
        files, label = logs.Files(place, name), process_label(name)
        if files.unseen_crash():
            lines.append(f"Crash of {label}: {_modified(files.crash)} (in {files.crash.name})")
        if files.last_crash.is_file():
            lines.append(f"Last crash of {label}: {_modified(files.last_crash)} (in {files.last_crash.name})")
        if files.last_error.is_file():
            lines.append(f"Last unexpected error in {label}: {_modified(files.last_error)} "
                         f"(in {files.last_error.name})")
    lines += ["", "Displays", *displays_lines()]
    return "\n".join(lines) + "\n"


def process_label(name: str) -> str:
    """"SnipForge", "the recorder", "the recorder (2)"."""
    if name == "snipforge":
        return "SnipForge"
    kind, _, n = name.partition("-")
    return f"the {kind}" + (f" ({n})" if n else "")


def _install_kind() -> str:
    return {"per-user": "installed for this user", "all-users": "installed for everyone on this PC",
            "source": "running from source"}.get(update_platform.install_mode(), "unknown install")


def _qt_line() -> str:
    try:
        import PySide6
        from PySide6.QtCore import qVersion
        from PySide6.QtGui import QGuiApplication
    except ImportError as exc:  # pragma: no cover - SnipForge can't run without it
        return f"Qt: unavailable ({exc})"
    plugin = QGuiApplication.platformName() if QGuiApplication.instance() is not None else "none"
    return f"Qt {qVersion()}, PySide6 {PySide6.__version__}, display plugin \"{plugin}\""


def operating_system() -> str:
    if sys.platform == "win32":
        try:
            from ._diagnostics_win32 import windows_version
            return windows_version()
        except OSError:
            return platform.platform()
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0] or platform.release()}"
    return platform.platform()


def _language() -> str:
    try:
        from PySide6.QtCore import QLocale
        return QLocale.system().name()
    except ImportError:  # pragma: no cover
        return locale.getlocale()[0] or "unknown"


def _count_folders(root: Path) -> str:
    try:  # scandir knows what's a folder without asking again (OneDrive files stay online-only)
        with os.scandir(root) as entries:
            count = sum(1 for entry in entries if entry.is_dir())
    except OSError:
        return "no folder yet"
    return f"{count:,} snips and recordings"


def _modified(path: Path) -> str:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "unknown"


def displays_lines() -> list[str]:
    """Each monitor: its size in pixels, scaling, refresh rate and where it sits."""
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.instance() is None:
        return ["Not known (no display connection)"]
    primary = QGuiApplication.primaryScreen()
    lines = []
    for n, screen in enumerate(QGuiApplication.screens(), 1):
        g, ratio = screen.geometry(), screen.devicePixelRatio()
        maker = " ".join(part for part in (screen.manufacturer(), screen.model()) if part).strip()
        lines.append(
            f"{n}. {round(g.width() * ratio)} x {round(g.height() * ratio)} pixels, {round(ratio * 100)}% scaling, "
            f"{screen.refreshRate():.0f} Hz, at ({g.x()}, {g.y()}) on the desktop"
            + (", the main display" if screen is primary else "") + (f" ({maker})" if maker else ""))
    return lines or ["None found"]


def settings_text(settings=None, policy_hives: Sequence[Mapping[str, object]] | None = None) -> str:
    """SnipForge's settings with private values left out, then any IT policy for updates."""
    store = settings if settings is not None else app_settings()
    lines = ["Settings (anything personal, or saying where your files are, is left out)"]
    keys = sorted(store.allKeys())
    lines += [f"{key} = {_shown(key, store.value(key))}" for key in keys] or ["(none changed from the defaults)"]
    hives = update_platform.read_policy_hives() if policy_hives is None else policy_hives
    lines += ["", "IT policy for updates (Software\\Policies\\SnipForge)"]
    named = [(label, hive) for label, hive in zip(("Machine", "User"), hives, strict=False) if hive]
    if not named:
        lines.append("(none set)" if hives else "(not read on this platform)")
    for label, hive in named:
        for name, value in sorted(hive.items()):
            lines.append(f"{label}: {name} = {_feed_kind(value) if name == 'UpdateFeed' else _shown(name, value)}")
    return "\n".join(lines) + "\n"


def _shown(key: str, value) -> str:
    if key.lower() in PRIVATE_KEYS or "title" in key.lower():
        return LEFT_OUT
    if isinstance(value, bool) or isinstance(value, int | float):
        return str(value).lower() if isinstance(value, bool) else str(value)
    if isinstance(value, str):
        return value if SAFE_VALUE.fullmatch(value) else f"(left out: {len(value)} characters)"
    if isinstance(value, list | tuple):
        if len(value) <= 12 and all(isinstance(v, str) and SAFE_VALUE.fullmatch(v) for v in value):
            return ", ".join(value) or "(empty list)"
        return f"(left out: a list of {len(value)})"
    return LEFT_OUT


def _feed_kind(value) -> str:
    text = str(value or "").strip().strip('"')
    if text.lower().startswith("https://"):
        return "(an https address; left out)"
    if text.startswith("\\\\"):
        return "(a network share; left out)"
    if text:
        return "(a folder; left out)"
    return "(empty)"


def log_text(files: logs.Files, limit: int = LOG_TAIL_BYTES) -> str:
    """The newest end of a process's logs, oldest line first, starting on a whole line."""
    data = b""
    for path in files.logs():
        try:
            data += path.read_bytes()
        except OSError:
            continue
    if len(data) > limit:
        data = data[-limit:]
        data = data[data.find(b"\n") + 1:] if b"\n" in data else data
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------------------------- the report

def build(*, log_folder: Path | None = None, library_root: Path | None = None, settings=None,
          policy_hives: Sequence[Mapping[str, object]] | None = None, environ: Mapping[str, str] | None = None,
          home: Path | None = None, hostnames: Sequence[str] | None = None, now: dt.datetime | None = None) -> Report:
    """Everything Report a problem shows, with the person's details taken out."""
    now = now or dt.datetime.now()
    folder = log_folder or logs.folder()
    hives = update_platform.read_policy_hives() if policy_hives is None else policy_hives
    scrub = Scrubber(environ, home, hosts=policy_hosts(hives), hostnames=hostnames)
    files = [ReportFile("summary.txt", "Summary: versions and displays", summary_text(now, library_root, folder)),
             ReportFile("settings.txt", "Settings, private values left out", settings_text(settings, hives))]
    for name in logs.names(folder):
        process, label = logs.Files(folder, name), process_label(name)
        if process.logs():
            files.append(ReportFile(process.log.name, f"Recent log of {label}", log_text(process)))
        if crash := process.unseen_crash():
            files.append(ReportFile(process.crash.name, f"Crash of {label}", crash))
        if process.last_crash.is_file():
            files.append(ReportFile(process.last_crash.name, f"Last crash of {label}", _read(process.last_crash)))
        if process.last_error.is_file():
            files.append(ReportFile(process.last_error.name, f"Last unexpected error in {label}",
                                    _read(process.last_error)))
    return Report(now, tuple(ReportFile(f.name, f.title, scrub(f.text)) for f in files))


def save(report: Report, path: Path) -> Path:
    """Write the report as a zip, in one go: a half-written file never takes its name."""
    path = Path(path)
    handle, partial = tempfile.mkstemp(prefix=".snipforge-report-", suffix=".partial", dir=path.parent)
    os.close(handle)
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for f in report.files:
                info = zipfile.ZipInfo(f.name, report.created.timetuple()[:6])
                info.external_attr = 0o644 << 16  # readable when unzipped on a Mac or Linux, not mode 000
                archive.writestr(info, f.text.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        os.replace(partial, path)
    except BaseException:
        Path(partial).unlink(missing_ok=True)
        raise
    return path
