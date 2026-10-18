"""Entry point for the packaged SnipForge.exe."""
import sys

if __name__ == "__main__":
    if "--record" in sys.argv:  # the recorder process SnipForge starts for each recording
        from snipforge.video.recorder_process import main
    else:
        from snipforge.app import main
    raise SystemExit(main())
