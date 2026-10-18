"""SnipForge's log and crash files, kept on this PC for Report a problem.

Each process keeps its own files in `folder()`, named after it (`snipforge`, or `recorder` for the
recording process):
- `<name>.log`, with what SnipForge's code logs, rotated at 1 MB with two older copies;
- `<name>.crash.txt`, where Python's faulthandler writes if the process dies (an access violation,
  say). If the process didn't quit normally, it's kept as `<name>.last-crash.txt` at the next start;
- `<name>.last-error.txt`, the most recent unexpected Python error (one SnipForge carried on after);
- `<name>.running`, there while the process runs, so the next start can tell it didn't quit normally.

Nothing here leaves the PC: Report a problem (snipforge.diagnostics) shows these files to the
person, who decides whether to attach them to a bug report.
"""
from __future__ import annotations

import datetime as dt
import faulthandler
import logging
import logging.handlers
import os
import sys
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path

LOG_BYTES = 1_000_000
LOG_BACKUPS = 2
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

log = logging.getLogger(__name__)


FOLDER_VARIABLE = "SNIPFORGE_LOG_FOLDER"  # tests point it at a throwaway folder (tests/conftest.py)


def folder() -> Path:
    """Where the log and crash files live: %LOCALAPPDATA%\\SnipForge\\logs, or ~/Library/Logs/SnipForge."""
    if os.environ.get(FOLDER_VARIABLE):
        return Path(os.environ[FOLDER_VARIABLE])
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "SnipForge"
    base = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
    return (Path(base) / "SnipForge" if base else Path.home() / ".cache" / "SnipForge") / "logs"


@dataclass(frozen=True)
class Files:
    """The files one process keeps (see the module docstring)."""
    folder: Path
    name: str

    def _named(self, suffix: str) -> Path:
        return self.folder / f"{self.name}{suffix}"

    @property
    def log(self) -> Path:
        return self._named(".log")

    @property
    def crash(self) -> Path:
        return self._named(".crash.txt")

    @property
    def last_crash(self) -> Path:
        return self._named(".last-crash.txt")

    @property
    def last_error(self) -> Path:
        return self._named(".last-error.txt")

    @property
    def running(self) -> Path:
        return self._named(".running")

    def logs(self) -> list[Path]:
        """The log and its rotated copies that exist, oldest first."""
        copies = [Path(f"{self.log}.{n}") for n in range(LOG_BACKUPS, 0, -1)]
        return [path for path in (*copies, self.log) if path.is_file()]


@dataclass
class LogSession:
    """One process's logging, from setup() until close()."""
    files: Files
    crashed_last_time: bool  # the last run didn't quit normally and left crash details
    handler: logging.Handler | None = None
    _crash_file: object = None
    _hooks: tuple = field(default=(), repr=False)

    def close(self) -> None:
        """The process is quitting normally: stop writing, and mark it as having ended well."""
        if self._crash_file is not None:
            faulthandler.disable()
            self._crash_file.close()
            self._crash_file = None
        if self.handler is not None:
            logging.getLogger().removeHandler(self.handler)
            self.handler.close()
            self.handler = None
        if self._hooks:
            sys.excepthook, threading.excepthook = self._hooks
            self._hooks = ()
        self.ending()

    def ending(self) -> None:
        """The process is about to end normally. Windows signing out or shutting down may stop it
        before close() runs, which mustn't look like a crash at the next start."""
        try:
            self.files.running.unlink(missing_ok=True)
        except OSError as exc:
            log.info("Couldn't mark SnipForge as having quit normally: %s", exc)


def setup(name: str = "snipforge", where: Path | None = None) -> LogSession | None:
    """Start writing this process's log and crash files. None if the folder can't be written
    (SnipForge works the same without them)."""
    files = Files(where or folder(), name)
    try:
        files.folder.mkdir(parents=True, exist_ok=True)
        crashed = _keep_last_crash(files)
        crash_file = open(files.crash, "w", encoding="utf-8")  # noqa: SIM115 - open for the whole run
    except OSError as exc:
        log.warning("SnipForge can't keep a log in %s: %s", files.folder, exc)
        return None
    session = LogSession(files, crashed)
    session._crash_file = crash_file
    faulthandler.enable(crash_file, all_threads=True)
    handler = logging.handlers.RotatingFileHandler(files.log, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS,
                                                   encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    session.handler = handler
    session._hooks = (sys.excepthook, threading.excepthook)
    sys.excepthook = lambda kind, error, trace: _unexpected(files, kind, error, trace, session._hooks[0])
    threading.excepthook = lambda args: _unexpected(files, args.exc_type, args.exc_value, args.exc_traceback,
                                                    None, args.thread)
    try:
        files.running.write_text(f"{os.getpid()} {_now()}\n", encoding="utf-8")
    except OSError as exc:
        log.info("Couldn't note that SnipForge is running: %s", exc)
    return session


def _keep_last_crash(files: Files) -> bool:
    """At start: if the last run didn't quit normally and faulthandler wrote something, keep it as
    the last crash. Details from a run that did quit normally are dropped: faulthandler also writes
    for some errors Windows handles itself, which aren't crashes."""
    details = _read(files.crash)
    ended_badly = files.running.exists()
    if ended_badly and details.strip():
        started = _read(files.running).strip()
        stamp = dt.datetime.fromtimestamp(files.crash.stat().st_mtime).isoformat(sep=" ", timespec="seconds")
        files.last_crash.write_text(
            f"SnipForge ({files.name}) stopped unexpectedly. Last written: {stamp}. "
            f"Started: {started.split(' ', 1)[-1] if started else 'unknown'}.\n\n{details}", encoding="utf-8")
        return True
    return False


def _unexpected(files: Files, kind, error, trace, previous, thread=None) -> None:
    """An error nothing caught: into the log, and kept as the last error."""
    if issubclass(kind, (KeyboardInterrupt, SystemExit)) and previous is not None:
        previous(kind, error, trace)
        return
    where = f" in thread {thread.name}" if thread is not None else ""
    logging.getLogger("snipforge").error("Unexpected error%s", where, exc_info=(kind, error, trace))
    try:
        files.last_error.write_text(f"{_now()}{where}\n\n" + "".join(traceback.format_exception(kind, error, trace)),
                                    encoding="utf-8")
    except OSError:
        pass  # the log has it


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _now() -> str:
    return dt.datetime.now().isoformat(sep=" ", timespec="seconds")
