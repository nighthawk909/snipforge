"""Screen recording (docs/VIDEO-PLAN.md).

Qt Multimedia is imported only inside these modules' functions, never at import time, so SnipForge
starts as fast as before; only recording, playing or exporting a video loads it.

Qt's own player and recorder are limited to local files, as SnipForge's direct use of FFmpeg is
(video/ffmpeg.py). Qt reads this once, when Qt Multimedia loads, which is always after this import.
"""
import os

os.environ.setdefault("QT_FFMPEG_PROTOCOL_WHITELIST", "file")
