"""Where recordings are saved: in the Library, a folder of their own like a snip's, named the same
way (when it was made), holding recording.mp4, and recording.json once it's finished, which is when
the Library starts listing it (library.py)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from ..library import RECORDING


def new_recording(root: Path) -> Path:
    """A fresh folder under `root` and the recording's path in it (the file isn't created)."""
    root.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:19]
    folder, suffix = root / stamp, 1
    while True:
        try:
            folder.mkdir()
            return folder / RECORDING
        except FileExistsError:
            folder = root / f"{stamp}-{suffix}"
            suffix += 1


def duration_text(seconds: float) -> str:
    """0:07, 4:32, 1:02:09."""
    whole = max(0, int(round(seconds)))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"

