"""Measure SnipForge's speed and footprint: start-up, snip to editor, idle memory and CPU, and a
Library of more than a thousand snips. Prints a table (or JSON with --json).

    python scripts/measure_performance.py [--json] [--snips 1200]

Each measurement runs in a fresh Python with throwaway settings and a made-up Library (never
yours), on Qt's offscreen platform, so nothing appears on screen and no keyboard hook is installed.
The installer's size comes from `python scripts/build.py --installer` (budget in scripts/build.py).
The packaged app checks its own budgets in `SnipForge.exe --self-test` (snipforge/selftest.py).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHILD = r'''
import json, os, sys, tempfile, time
started = time.perf_counter()
sys.path.insert(0, {root!r})
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["SNIPFORGE_LOG_FOLDER"] = tempfile.mkdtemp()
from PySide6.QtCore import QSettings
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp())
from snipforge import perf
results = perf.{measure}({args})
results["python_start_to_measure_s"] = round(time.perf_counter() - started, 3)
print("RESULT " + json.dumps(results))
'''


def run(measure: str, args: str = "") -> dict:
    code = CHILD.format(root=str(ROOT), measure=measure, args=args)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600, cwd=ROOT)
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("RESULT ")), None)
    if line is None:
        raise RuntimeError(f"{measure} failed:\n{done.stdout}\n{done.stderr}")
    return json.loads(line[len("RESULT "):])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--snips", type=int, default=1200)
    parser.add_argument("--runs", type=int, default=3, help="start-ups to time (the median is reported)")
    options = parser.parse_args()
    starts = sorted((run("startup") for _ in range(options.runs)), key=lambda r: r["startup_s"])
    results = {"startup": starts[len(starts) // 2], "snip_to_editor": run("snip_to_editor"),
               "idle": run("idle"), "big_library": run("big_library", f"snips={options.snips}")}
    if options.json:
        print(json.dumps(results, indent=2))
        return 0
    for name, values in results.items():
        print(name)
        for key, value in values.items():
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    tempfile.tempdir = None
    sys.exit(main())
