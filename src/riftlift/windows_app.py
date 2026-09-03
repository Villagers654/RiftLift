"""Console-free Windows application entry point, also used by the installer."""

from __future__ import annotations

import ctypes
import os
import sys
import traceback
from pathlib import Path

from .config import Paths


def main() -> int:
    # pythonw and PyInstaller's windowed bootloader have no standard streams.
    # Keep startup failures accessible even before the Activity widget exists.
    paths = Paths.defaults()
    log = paths.data / "logs" / "riftlift.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if not sys.stdout or not sys.stderr:
        if log.exists() and log.stat().st_size > 2 * 1024 * 1024:
            log.replace(log.with_suffix(".previous.log"))
        stream = log.open("a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = stream
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Villagers654.RiftLift"
        )
        if len(sys.argv) == 3 and sys.argv[1] == "--package-check":
            from .windows_package_check import run

            return run(Path(sys.argv[2]))
        from .windows import main as command

        return command(sys.argv[1:] or ["gui"])
    except Exception:
        traceback.print_exc()
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            ctypes.windll.user32.MessageBoxW(
                None,
                f"RiftLift could not start. Please reinstall RiftLift.\n\nDetails: {log}",
                "RiftLift",
                0x10,
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
