"""Adapt the native host to the existing main_window service signatures.

No widgets, styles, or replacement windows live here.
"""

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
    backend = windows.select_backend(game)
    result = windows.launch(paths, game, backend, extra=arguments)
    if result:
        raise RiftLiftError(
            f"Native game launch exited with code {result} "
            f"(0x{result & 0xffffffff:08X}). Log: {paths.data / 'logs' / (game.slug + '.log')}"
        )
    return result
