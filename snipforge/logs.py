"""SnipForge's log and crash files, kept on this PC for Report a problem.

Each process keeps its own files in `folder()`, named after it (`snipforge`, or `recorder` for the
recording process; `recorder-2` and so on when a recorder starts while another is still finishing):
- `<name>.log`, with what SnipForge's code logs, rotated at 1 MB with two older copies;
- `<name>.crash.txt`, where Python's faulthandler writes if the process dies (an access violation,
  say). If the process didn't quit normally, it's kept as `<name>.last-crash.txt` at the next start;
- `<name>.last-error.txt`, the most recent unexpected Python error (one SnipForge carried on after);
- `<name>.running`, with the process id, there while the process runs, so the next start (and
  Report a problem) can tell it didn't quit normally.

Nothing here leaves the PC: Report a problem (snipforge.diagnostics) shows these files to the
person, who decides whether to attach them to a bug report.
"""
from __future__ import annotations

import collections
import datetime as dt
import faulthandler
import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

LOG_BYTES = 1_000_000
LOG_BACKUPS = 2
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
FOLDER_VARIABLE = "SNIPFORGE_LOG_FOLDER"  # tests point it at a throwaway folder (tests/conftest.py)
SLOTS = 4  # processes of one kind keeping files at the same time (a recorder still finishing, say)
ERRORS_PER_MINUTE = 10  # unexpected errors written out in full; more in a minute are only counted
REVIVE_AFTER_S = 60  # a sign-out that's still not happened after this was cancelled
NOTED_MAX = 4000  # characters of a handled Windows error copied into the log

log = logging.getLogger(__name__)


def folder() -> Path:
    """Where the log and crash files live: %LOCALAPPDATA%\\SnipForge\\logs."""
    if os.environ.get(FOLDER_VARIABLE):
        return Path(os.environ[FOLDER_VARIABLE])
    base = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
    return (Path(base) / "SnipForge" if base else Path.home() / ".cache" / "SnipForge") / "logs"


def process_alive(pid: int) -> bool:
    """Whether process `pid` is running (never signals it: on Windows os.kill would end it)."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        from ._process_win32 import alive
        return alive(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # someone else's process: it exists
    except OSError:
        return False
    return True


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

    def owner(self) -> int | None:
        """The process id in the running marker, if there is one."""
        try:
            return int(_read(self.running).split()[0])
        except (IndexError, ValueError):
            return None

    def ended_badly(self) -> bool:
        """A run left its marker behind and isn't running any more: it didn't quit normally."""
        owner = self.owner()
        return self.running.exists() and (owner is None or not process_alive(owner))

    def unseen_crash(self) -> str:
        """Crash details of a run that ended badly and that no start has kept yet (a recorder that
        crashed is only started again with the next recording)."""
        details = _read(self.crash)
        return details if details.strip() and self.ended_badly() else ""


def names(where: Path) -> list[str]:
    """The processes keeping files in `where`: SnipForge's first, then its recorders."""
    try:
        found = {entry.name.split(".", 1)[0] for entry in os.scandir(where) if entry.is_file()}
    except OSError:
        return []
    return sorted(found, key=lambda name: (name != "snipforge", name))


@dataclass
class LogSession:
    """One process's logging, from setup() until close()."""
    files: Files
    crashed_last_time: bool  # the last run didn't quit normally and left crash details
    handler: logging.Handler | None = None
    _crash_file: object = None
    _hooks: tuple = field(default=(), repr=False)  # (sys.excepthook, threading.excepthook) before ours
    _ours: tuple = field(default=(), repr=False)
    _ending_at: float | None = None

    def tick(self) -> None:
        """Every half a minute or so while running. faulthandler output that appeared while the
        process lived on was for an error Windows handled, not a crash: it goes into the log, and
        the crash file is emptied so it can't look like a crash later. And a marker taken away for
        a sign-out or shutdown that didn't happen (another app cancelled it) is put back."""
        if self._crash_file is not None:
            try:
                size = os.fstat(self._crash_file.fileno()).st_size
            except (OSError, ValueError):
                size = 0
            if size:
                log.info("Windows reported an error SnipForge carried on after:\n%s",
                         _read(self.files.crash).strip()[:NOTED_MAX])
                try:
                    self._crash_file.seek(0)
                    self._crash_file.truncate()
                except (OSError, ValueError) as exc:
                    log.info("Couldn't empty the crash file: %s", exc)
        if self._ending_at is not None and time.monotonic() - self._ending_at > REVIVE_AFTER_S:
            self._ending_at = None
            _mark_running(self.files)

    def ending(self) -> None:
        """The process is about to end normally. Windows signing out or shutting down may stop it
        before close() runs, which mustn't look like a crash at the next start."""
        self._ending_at = time.monotonic()
        try:
            self.files.running.unlink(missing_ok=True)
        except OSError as exc:
            log.info("Couldn't mark SnipForge as having quit normally: %s", exc)

    def close(self) -> None:
        """The process is quitting normally: stop writing, and mark it as having ended well."""
        global _active
        if self._crash_file is not None:
            faulthandler.disable()
            self._crash_file.close()
            self._crash_file = None
        if self.handler is not None:
            logging.getLogger().removeHandler(self.handler)
            self.handler.close()
            self.handler = None
        if self._hooks:
            ours_main, ours_threads = self._ours
            if sys.excepthook is ours_main:  # not one installed after ours (the recorder's)
                sys.excepthook = self._hooks[0]
            if threading.excepthook is ours_threads:
                threading.excepthook = self._hooks[1]
            self._hooks = self._ours = ()
        if _active is self:
            _active = None
        self.ending()
        self._ending_at = None


_active: LogSession | None = None
_recent_errors: collections.deque = collections.deque(maxlen=ERRORS_PER_MINUTE)
_skipped_errors = 0


def setup(name: str = "snipforge", where: Path | None = None, slots: int = 1) -> LogSession | None:
    """Start writing this process's log and crash files. None if the folder can't be written
    (SnipForge works the same without them). `slots`: how many processes of this kind can run at
    once (SLOTS for recorders). SnipForge itself runs one copy at a time, so any marker it finds is
    from a run that's over, even if another process has since been given that process id."""
    global _active
    place = where or folder()
    try:
        place.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("SnipForge can't keep a log in %s: %s", place, exc)
        return None
    files = _free_slot(place, name, slots)
    try:
        crashed = _keep_last_crash(files)
    except OSError as exc:
        log.info("Couldn't keep the last crash's details: %s", exc)
        crashed = False
    session = LogSession(files, crashed)
    try:
        session._crash_file = open(files.crash, "w", encoding="utf-8")  # noqa: SIM115 - open all run
        faulthandler.enable(session._crash_file, all_threads=True)
    except OSError as exc:
        log.info("Crashes won't be recorded: %s", exc)
        session._crash_file = None
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
    previous = sys.excepthook

    def main_hook(kind, error, trace):
        _unexpected(files, kind, error, trace, previous)

    def thread_hook(args):
        _unexpected(files, args.exc_type, args.exc_value, args.exc_traceback, None, args.thread)

    sys.excepthook, threading.excepthook = main_hook, thread_hook
    session._ours = (main_hook, thread_hook)
    _mark_running(files)
    _active = session
    return session


def remember_error(kind, error, trace) -> None:
    """For a process that handles unexpected errors itself (the recorder): keep this one as its
    last error, as SnipForge's own handler would."""
    if _active is not None:
        _write_error(_active.files, kind, error, trace, "")


def _free_slot(place: Path, name: str, slots: int) -> Files:
    """The first of `name`, `name-2`... whose last user isn't running any more."""
    if slots <= 1:
        return Files(place, name)
    for n in range(1, slots + 1):
        files = Files(place, name if n == 1 else f"{name}-{n}")
        owner = files.owner()
        if owner is None or owner == os.getpid() or not process_alive(owner):
            return files
    return Files(place, f"{name}-{os.getpid()}")


def _mark_running(files: Files) -> None:
    try:
        files.running.write_text(f"{os.getpid()} {_now()}\n", encoding="utf-8")
    except OSError as exc:
        log.info("Couldn't note that SnipForge is running: %s", exc)


def _keep_last_crash(files: Files) -> bool:
    """At start: if the last run didn't quit normally and faulthandler wrote something, keep it as
    the last crash. Details from a run that did quit normally are dropped: faulthandler also writes
    for some errors Windows handles itself, which aren't crashes."""
    details = _read(files.crash)
    if files.running.exists() and details.strip():
        started = _read(files.running).strip()
        stamp = dt.datetime.fromtimestamp(files.crash.stat().st_mtime).isoformat(sep=" ", timespec="seconds")
        files.last_crash.write_text(
            f"SnipForge ({files.name}) stopped unexpectedly. Last written: {stamp}. "
            f"Started: {started.split(' ', 1)[-1] if ' ' in started else 'unknown'}.\n\n{details}", encoding="utf-8")
        return True
    return False


def _unexpected(files: Files, kind, error, trace, previous, thread=None) -> None:
    """An error nothing caught: into the log, and kept as the last error. A flood (a timer failing
    every few milliseconds) is counted rather than written out each time."""
    global _skipped_errors
    if issubclass(kind, SystemExit) and thread is not None:
        return  # how a thread ends itself, as Python's own handler treats it
    if issubclass(kind, (KeyboardInterrupt, SystemExit)) and previous is not None:
        previous(kind, error, trace)
        return
    now = time.monotonic()
    if len(_recent_errors) == _recent_errors.maxlen and now - _recent_errors[0] < 60:
        _skipped_errors += 1
        return
    _recent_errors.append(now)
    where = f" in thread {thread.name}" if thread is not None else ""
    skipped, _skipped_errors = _skipped_errors, 0
    more = f" ({skipped} more not written out)" if skipped else ""
    logging.getLogger("snipforge").error("Unexpected error%s%s", where, more, exc_info=(kind, error, trace))
    _write_error(files, kind, error, trace, where)


def _write_error(files: Files, kind, error, trace, where: str) -> None:
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
