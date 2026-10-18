# Speed and footprint

How fast SnipForge starts and opens things, how much it takes from the PC, and the budgets that keep
it that way. Measured with `python scripts/measure_performance.py` (a made-up Library, throwaway
settings, Qt's offscreen platform, on a 16-thread laptop running Windows 11), and the installer by
the release build.

| | Before Phase 5 (0.12.0) | Now | Budget | Checked by |
|---|---|---|---|---|
| Start-up: import to the Library painted | 0.52 s | 0.30 s | 3 s | test suite (`tests/test_performance.py`) |
| Modules loaded at start-up | 345 | 260 (265 packaged) | 300 on Windows, and none of the heavy ones anywhere | test suite, packaged self-test |
| Memory at start-up | 47 MB | 39 MB | 150 MB | test suite |
| Snip to editor (1920 × 1080, saved to the Library, editor painted) | 0.14–0.20 s | 0.18 s (0.12 s packaged) | 1.5 s | packaged self-test |
| Idle in the tray: memory, CPU | 44 MB, 0 % | 40 MB, 0 % | | |
| Library of 1,200 snips: opening its window | 0.57 s | 0.46 s (0.43 s packaged, 1,000 snips) | 3 s (1,000 snips) | packaged self-test |
| Library of 1,200 snips: opening it again | 0.20 s | 0.05 s (0.02 s packaged) | 1 s | packaged self-test |
| Installer | 38.9 MB | about 36 MB (the release build's figure is in its log) | 38 MB | the build (`scripts/build.py`) |

The budgets are in `snipforge/perf.py` (`BUDGETS`) and, for the installer, `scripts/build.py`. Timing
budgets leave room for slow CI machines: they catch a change that makes something several times
slower, not noise. "None of the heavy ones" means the editor, recording, Qt Multimedia, Pillow,
asyncio (for text recognition), Outlook automation and Report a problem load when they're first used,
not when SnipForge starts. The Mac's start-up also loads PyObjC's modules, so there only the heavy
ones are checked until the Mac port measures it.

## What changed

- **Start-up loads less.** The editor's code (a quarter of a second) loads 1.5 s after SnipForge
  starts, once it's showing, instead of before; text recognition's asyncio (a tenth of a second)
  loads when text is first read.
- **The Library reads its snips several at a time.** Listing each snip's folder and reading its
  details wait on the disk and antivirus, not on Python, so eight at a time is faster with
  thousands of snips; a snip's date comes from its name without `strptime`.
- **The Library doesn't read what hasn't changed.** A snip's folder is listed again only when its
  modification time says something in it changed (every write renames a finished file into place,
  which updates it); folders changed in the last few seconds are always listed.
- **The installer leaves out what SnipForge never uses:** Python's own OpenSSL (SnipForge goes online
  only through Qt, which brings its own), Qt's translations of its dialogs (SnipForge never loads a
  translator), the Direct2D, minimal and touch-over-network plugins, and zstd archives.
  `tests/test_packaging.py` fails if SnipForge starts needing one of them.

## Measuring

```bash
python scripts/measure_performance.py          # the table above, without the installer
python scripts/measure_performance.py --json   # the same as JSON
python scripts/build.py --installer            # prints the installer's size against its budget
dist/SnipForge/SnipForge.exe --self-test r.json # the packaged app checks its budgets
```

A Library made a moment ago is slower to open the first time than one that's been on disk a while:
Windows' antivirus scans each new file the first time it's read (reading 1,200 new files took
9 s once, and 0.15 s after that). The measurement reads every file once first, as a Library in use
has been.
