"""Stable entry point for RiftLift's desktop application."""

from __future__ import annotations

import sys


def main() -> int:
    if "--download-worker" in sys.argv[1:]:
        from .download_worker import main as worker_main

        return worker_main()
    if "--fixture" in sys.argv[1:]:
        from .ui_preview import main as fixture_main

        return fixture_main([arg for arg in sys.argv[1:] if arg != "--fixture"])
    try:
        from .main_window import main as window_main
    except ModuleNotFoundError as error:
        if not error.name or not error.name.startswith("PySide6"):
            raise
        print(f"RiftLift's GUI needs Qt 6: {error}")
        return 1
    return window_main()


if __name__ == "__main__":
    raise SystemExit(main())
