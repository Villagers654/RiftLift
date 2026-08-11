from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from .config import Game, Paths
from .detection import uses_openvr_runtime
from .runtime import install_proton, install_revive, launch_environment
from .util import RiftLiftError, linux_to_windows


def revive_backend(game: Game) -> str:
    """Select a translation path from install capabilities, never a title list."""
    override = os.environ.get("RIFTLIFT_REVIVE_BACKEND", "").strip().lower()
    if override:
        if override not in {"openxr", "openvr"}:
            raise RiftLiftError(
                "RIFTLIFT_REVIVE_BACKEND must be 'openxr' or 'openvr'"
            )
        return override

    # Games shipping both Oculus and OpenVR integrations generally depend on
    # the mature compositor/overlay behavior in classic Revive. Oculus-only
    # installs take the shorter ReviveXR path. This static capability probe is
    # deterministic, adds no failed first launch, and contains no title rules.
    return "openvr" if uses_openvr_runtime(game.game_dir) else "openxr"


def launch(paths: Paths, game: Game, extra_arguments: list[str]) -> int:
    if not game.executable_path.is_file():
        raise RiftLiftError(f"game executable is missing: {game.executable_path}")
    revive = install_revive(paths)
    proton = install_proton(paths) / "proton"
    backend = revive_backend(game)
    arguments = [
        str(proton),
        # Proton deliberately skips OpenVR path/runtime setup for its
        # runinprefix maintenance verb. Classic Revive needs the normal game
        # verb so Proton maps VR_OVERRIDE into C:\\vrclient; ReviveXR uses
        # WineOpenXR directly and keeps the lightweight existing-prefix path.
        "run" if backend == "openvr" else "runinprefix",
        str(revive / "ReviveInjector.exe"),
        "/wait",
        f"/{backend}",
        "/app",
        game.app_key,
        linux_to_windows(game.executable_path),
        *game.arguments,
        *extra_arguments,
    ]
    verified_rift_download = not game.app_key.startswith("steam.app.")
    openvr_runtime = os.environ.get("VR_OVERRIDE", "").strip()
    environment = launch_environment(
        paths,
        game.game_dir,
        game.platform_shim,
        game.platform_offline or verified_rift_download,
    )
    if game.steam_app_id is not None:
        # Steam-distributed Oculus builds may still use Steamworks for DRM,
        # ownership, saves, or startup. Rift-store games deliberately keep the
        # isolated zero identity supplied by proton_environment.
        steam_id = str(game.steam_app_id)
        environment["SteamAppId"] = steam_id
        environment["SteamGameId"] = steam_id
    if backend == "openxr":
        # Proton records OpenVR's Vulkan requirements in the shared Wine
        # prefix. DXVK otherwise consumes those stale requirements alongside
        # WineOpenXR's and can request host-only extensions from Wine's Vulkan
        # device, making D3D device creation fail before the title reaches XR.
        # Keep the selected backend authoritative while leaving explicit
        # OpenVR diagnostic launches untouched.
        environment["DXVK_NO_VR"] = "1"
    elif openvr_runtime:
        # proton_environment intentionally removes inherited OpenVR state so
        # direct ReviveXR launches cannot be polluted by it. Classic Revive is
        # itself an OpenVR client, however, so preserve the caller's selected
        # OpenVR-to-OpenXR runtime (normally XRizer) for this backend only.
        environment["VR_OVERRIDE"] = openvr_runtime
    wrapper_value = os.environ.get("RIFTLIFT_LAUNCH_WRAPPER", "").strip()
    wrapper: list[str] = []
    if wrapper_value:
        wrapper = shlex.split(wrapper_value)
        if not wrapper or not shutil.which(wrapper[0]):
            raise RiftLiftError(
                f"configured launch wrapper was not found: {wrapper_value}"
            )
    print(
        f"Launching {game.name} through "
        f"{'ReviveXR -> WineOpenXR' if backend == 'openxr' else 'Revive -> OpenVR bridge'} "
        "-> active OpenXR runtime..."
    )
    return subprocess.call([*wrapper, *arguments], cwd=game.game_dir, env=environment)
