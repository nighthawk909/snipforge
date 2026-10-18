"""Where recordings are saved: in the Library, a folder of their own like a snip's, named the same
way (when it was made), holding recording.mp4, and recording.json once it's finished, which is when
the Library starts listing it (library.py)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

RECORDING = "recording.mp4"  # library.RECORDING
DETAILS = "recording.json"  # library.RECORDING_DETAILS: written once the recording is finished


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


def write_details(path: Path, duration: float, size: tuple[int, int]) -> None:
    """Mark a Library recording finished, with its length and size: the Library lists it from then
    on. Written whole or not at all; nothing is written beside a file not named as a recording."""
    path = Path(path)
    if path.name != RECORDING:
        return
    target = path.with_name(DETAILS)
    tmp = target.with_name(DETAILS + ".tmp")
    data = {"duration": round(float(duration), 3), "width": int(size[0]), "height": int(size[1]), "version": 1}
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:  # the recording itself is safe; it just isn't listed until this works
        log.warning("Couldn't mark %s finished for the Library: %s", path, exc)
