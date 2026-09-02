"""Adapt the native host to the existing main_window service signatures.

No widgets, styles, or replacement windows live here.
"""

import os

from . import windows
from .config import Game, Paths
from .util import RiftLiftError


def doctor(paths: Paths) -> int:
    report, status = windows.doctor(paths)
    print(report)
    if status:
        raise RiftLiftError("Windows VR setup needs attention; see View Activity.")
    return status


def launch(paths: Paths, game: Game, arguments: list[str]) -> int:
    backend = os.environ.get("RIFTLIFT_WINDOWS_BACKEND") or (
        "openxr" if windows.runtime_ready("openxr") else "openvr"
    )
    result = windows.launch(paths, game, backend, extra=arguments)
    if result:
        raise RiftLiftError(
            f"Native game launch exited with code {result}; see View Activity."
        )
    return result
