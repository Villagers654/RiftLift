from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from .config import Game, Paths
from .detection import uses_d3d12_runtime, uses_openvr_runtime
from .runtime import install_proton, install_revive, launch_environment
from .util import RiftLiftError, linux_to_windows


def revive_backend(game: Game) -> str:
    """Select a translation path from install capabilities, never a title list."""
    override = os.environ.get("RIFTLIFT_REVIVE_BACKEND", "").strip().lower()
    if override:
        if override not in {"openxr", "openvr"}:
            raise RiftLiftError("RIFTLIFT_REVIVE_BACKEND must be 'openxr' or 'openvr'")
        return override

    # Games shipping both Oculus and OpenVR integrations generally depend on
    # the mature compositor/overlay behavior in classic Revive. D3D12 Oculus
    # clients also need that path because direct ReviveXR currently supports
    # D3D11. Other Oculus-only installs take the shorter ReviveXR path. These
    # static capability probes are deterministic, add no failed first launch,
    # and contain no title rules.
    needs_classic_revive = uses_openvr_runtime(game.game_dir) or uses_d3d12_runtime(
        game.executable_path
    )
    return "openvr" if needs_classic_revive else "openxr"


def launch(paths: Paths, game: Game, extra_arguments: list[str]) -> int:
    if not game.executable_path.is_file():
        raise RiftLiftError(f"game executable is missing: {game.executable_path}")
    revive = install_revive(paths)
    proton = install_proton(paths) / "proton"
    backend = revive_backend(game)
    arguments = [
        str(proton),
        # Use Proton's full game verb so it materializes the selected OpenVR
        # runtime in the prefix. Non-Steam launches select Proton's supported
        # umu.exe path below, which preserves the caller's working directory
        # without routing the injector through steam.exe.
        "run",
        str(revive / "ReviveInjector.exe"),
        "/wait",
        f"/{backend}",
        "/app",
        game.app_key,
        "/cwd",
        linux_to_windows(game.game_dir),
        linux_to_windows(game.executable_path),
        *game.arguments,
        *extra_arguments,
    ]
    verified_rift_download = game.source == "meta"
    openvr_runtime = os.environ.get("VR_OVERRIDE", "").strip()
    environment = launch_environment(
        paths,
        game.game_dir,
        game.platform_shim,
        game.platform_offline or verified_rift_download,
    )
    if game.steam_app_id:
        # Steam-distributed Oculus builds may still use Steamworks for DRM,
        # ownership, saves, or startup. Rift-store games deliberately keep the
        # isolated zero identity supplied by proton_environment.
        steam_id = str(game.steam_app_id)
        environment["SteamAppId"] = steam_id
        environment["SteamGameId"] = steam_id
    else:
        # GE-Proton's generic non-Steam entry point avoids its steam.exe shim
        # while retaining normal prefix and VR-runtime initialization. A
        # shared compatibility prefix is intentional, so use UMU's documented
        # neutral identity instead of inventing per-title database entries.
        environment["UMU_ID"] = "umu-default"
        environment["UMU_USE_STEAM"] = "0"
    if backend == "openxr":
        # Proton records OpenVR's Vulkan requirements in the shared Wine
        # prefix. DXVK otherwise consumes those stale requirements alongside
        # WineOpenXR's and can request host-only extensions from Wine's Vulkan
        # device, making D3D device creation fail before the title reaches XR.
        # Keep the selected backend authoritative while leaving explicit
        # OpenVR diagnostic launches untouched.
        environment["DXVK_NO_VR"] = "1"
    else:
        # Revive's Windows registry fallback points at a Wine path, while the
        # OpenVR implementation consuming it is a native Linux library. Give
        # XRizer the host path explicitly so classic Revive always loads the
        # bundled actions and controller bindings.
        environment["REVIVE_ACTION_MANIFEST"] = str(
            revive / "Input" / "action_manifest.json"
        )
    if backend == "openvr" and openvr_runtime:
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
