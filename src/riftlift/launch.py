from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from .config import Game, Paths
from .runtime import install_proton, install_revive, launch_environment
from .util import RiftLiftError


def launch(paths: Paths, game: Game, extra_arguments: list[str]) -> int:
    if not game.executable_path.is_file():
        raise RiftLiftError(f"game executable is missing: {game.executable_path}")
    revive = install_revive(paths)
    proton = install_proton(paths) / "proton"
    arguments = [
        str(proton),
        "run",
        str(revive / "ReviveInjector.exe"),
        "/openxr",
        "/app",
        game.app_key,
        str(game.executable_path),
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
