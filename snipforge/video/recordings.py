"""Where recordings are saved: in the Library, a folder of their own like a snip's, named the same
way (when it was made), holding recording.mp4. The Library lists only folders with a snip's image in
them for now, so these don't show there until it learns about recordings."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

RECORDING = "recording.mp4"


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
