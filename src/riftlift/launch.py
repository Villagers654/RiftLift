from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from .config import Game, Paths
from .runtime import install_proton, install_revive, launch_environment
from .util import RiftLiftError, linux_to_windows


def launch(paths: Paths, game: Game, extra_arguments: list[str]) -> int:
    if not game.executable_path.is_file():
        raise RiftLiftError(f"game executable is missing: {game.executable_path}")
    revive = install_revive(paths)
    proton = install_proton(paths) / "proton"
    arguments = [
        str(proton),
        # A persistent Horizon Link prefix always has a Proton wineserver. The
        # normal `run` verb serializes through Proton's Steam shim and can wait
        # forever behind that long-lived process; `runinprefix` starts the game
        # in the existing prefix without that unrelated launch lock.
        "runinprefix",
        str(revive / "ReviveInjector.exe"),
        # Keep the launch wrapper alive until the injected title exits. This
        # preserves compositor ownership and prevents WayVR/Steam from being
        # restored on top of a game whose injector has already detached.
        "/wait",
        "/openxr",
        "/app",
        game.app_key,
        # ReviveInjector passes this string directly to CreateProcessW. Proton
        # converts its own executable argument, but it cannot infer that a later
        # positional argument is another executable. Always give the injector a
        # Windows path so fresh prefixes do not stall on a Unix pathname.
        linux_to_windows(game.executable_path),
        *game.arguments,
        *extra_arguments,
    ]
    environment = launch_environment(paths, game.game_dir, game.platform_shim, game.platform_offline)
    wrapper_value = os.environ.get("RIFTLIFT_LAUNCH_WRAPPER", "").strip()
    if not wrapper_value:
        # Headset integrations can own runtime startup and dashboard handoff
        # without RiftLift depending on a specific device.
        automatic = shutil.which("psvr2-fossvr-run")
        wrapper = [automatic] if automatic else []
    else:
        wrapper = shlex.split(wrapper_value)
        if not wrapper or not shutil.which(wrapper[0]):
            raise RiftLiftError(f"configured launch wrapper was not found: {wrapper_value}")
    print(f"Launching {game.name} through ReviveXR -> WineOpenXR -> Monado...")
    return subprocess.call([*wrapper, *arguments], cwd=game.game_dir, env=environment)
