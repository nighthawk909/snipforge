import sys

if "--record" in sys.argv:  # the recorder process SnipForge starts for each recording
    from .video.recorder_process import main
else:
    from .app import main

sys.exit(main())
