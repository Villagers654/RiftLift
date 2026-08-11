from __future__ import annotations

import os
import shlex
import shutil
import subprocess

from .config import Game, Paths
from .diagnostics import launch_finished, launch_started
from .detection import (
    is_unity_player,
    is_unreal_shipping,
    uses_d3d12_runtime,
    uses_oculus_xr_plugin,
    uses_openvr_runtime,
)
from .playtime import PlaytimeSession
from .runtime import (
    install_proton,
    install_openvr_runtime,
    install_rift_runtime,
    launch_environment,
    native_xr_bridge,
)
from .util import RiftLiftError, linux_to_windows


def runtime_backend(game: Game) -> str:
    """Select a translation path from install capabilities, never a title list."""
    override = os.environ.get("RIFTLIFT_RUNTIME_BACKEND", "").strip().lower()
    if override:
        if override not in {"openxr", "openvr"}:
            raise RiftLiftError("RIFTLIFT_RUNTIME_BACKEND must be 'openxr' or 'openvr'")
        return override

    # Games shipping both Oculus and OpenVR integrations generally depend on
    # the mature OpenVR compositor behavior. D3D12 Oculus clients also need
    # that path because the direct OpenXR bridge cannot currently
    # establish their graphics session reliably. Other Oculus-only installs
    # take the shorter OpenXR path. These are capability probes, not titles.
    needs_openvr = (
        uses_openvr_runtime(game.game_dir)
        or uses_oculus_xr_plugin(game.game_dir)
        or uses_d3d12_runtime(game.executable_path)
    )
    return "openvr" if needs_openvr else "openxr"


def oculus_launch_arguments(game: Game, extra_arguments: list[str]) -> list[str]:
    """Select the installed engine's Oculus mode without title-specific rules."""
    arguments = [*game.arguments, *extra_arguments]
    lowered = [argument.casefold() for argument in arguments]
    if is_unreal_shipping(game.executable_path):
        # Unreal's -vr starts stereo rendering while -oculus resolves installs
        # that also ship an OpenVR plugin to the runtime RiftLift injected.
        arguments = [
            argument
            for argument in arguments
            if argument.casefold() not in {"-openvr", "-steamvr", "-openxr"}
        ]
        lowered = [argument.casefold() for argument in arguments]
        if "-vr" not in lowered:
            arguments.append("-vr")
        if "-oculus" not in lowered:
            arguments.append("-oculus")
    elif is_unity_player(game.executable_path):
        # Unity's engine-level selector is a two-argument option. Replace an
        # inherited SteamVR/OpenVR choice rather than stacking contradictory
        # runtime requests on the same process.
        normalized: list[str] = []
        index = 0
        while index < len(arguments):
            if arguments[index].casefold() == "-vrmode":
                index += 2
                continue
            normalized.append(arguments[index])
            index += 1
        arguments = [*normalized, "-vrmode", "Oculus"]
    return arguments


def launch(paths: Paths, game: Game, extra_arguments: list[str]) -> int:
    if not game.executable_path.is_file():
        raise RiftLiftError(f"game executable is missing: {game.executable_path}")
    rift_runtime = install_rift_runtime(paths)
    proton_root = install_proton(paths)
    proton = proton_root / "proton"
    backend = runtime_backend(game)
    native_bridge = native_xr_bridge(proton_root, backend)
    game_arguments = oculus_launch_arguments(game, extra_arguments)
    arguments = [
        str(proton),
        # Use Proton's full game verb so it materializes the selected runtime
        # and its graphics requirements consistently in the shared prefix.
        "run",
        str(rift_runtime / "RiftLiftLauncher.exe"),
        "/wait",
        f"/{backend}",
        "/app",
        game.app_key,
        "/cwd",
        linux_to_windows(game.game_dir),
        linux_to_windows(game.executable_path),
        *game_arguments,
    ]
    verified_rift_download = game.source == "meta"
    openvr_runtime = os.environ.get("VR_OVERRIDE", "").strip()
    if backend == "openvr" and not openvr_runtime:
        openvr_runtime = str(install_openvr_runtime(paths))
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
    if backend == "openxr" or not uses_d3d12_runtime(game.executable_path):
        # Proton records OpenVR's Vulkan requirements in the shared Wine
        # prefix. DXVK otherwise consumes those stale requirements alongside
        # WineOpenXR's and can request host-only extensions from Wine's Vulkan
        # device, making D3D device creation fail before the title reaches XR.
        # Keep the selected backend authoritative while leaving explicit
        # OpenVR diagnostic launches untouched.
        environment["DXVK_NO_VR"] = "1"
    if backend == "openvr":
        # The Windows bridge's registry fallback points at a Wine path, while the
        # OpenVR implementation consuming it is a native Linux library. Give
        # XRizer the host path explicitly so the OpenVR bridge always loads the
        # bundled actions and controller bindings.
        environment["RIFTLIFT_ACTION_MANIFEST"] = str(
            rift_runtime / "Input" / "action_manifest.json"
        )
    if backend == "openvr":
        # proton_environment intentionally removes inherited OpenVR state so
        # direct OpenXR launches cannot be polluted by it. The other bridge is
        # an OpenVR client, however, so preserve the caller's selected
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
        f"RiftLift native {backend.upper()} runtime -> headset..."
    )
    capabilities = [
        name
        for name, detected in (
            ("openvr", uses_openvr_runtime(game.game_dir)),
            ("oculus-xr-plugin", uses_oculus_xr_plugin(game.game_dir)),
            ("d3d12", uses_d3d12_runtime(game.executable_path)),
            ("unity", is_unity_player(game.executable_path)),
            ("unreal", is_unreal_shipping(game.executable_path)),
        )
        if detected
    ]
    launch_id, started = launch_started(
        paths,
        game,
        backend,
        wrapper=bool(wrapper),
        capabilities=capabilities,
    )
    print(
        "Native XR bridge: "
        f"{native_bridge.pe.name} -> {native_bridge.unix.name} (Wine unixlib)"
    )
    playtime_session: PlaytimeSession | None = None
    try:
        try:
            playtime_session = PlaytimeSession(paths, game.slug)
        except OSError as error:
            print(f"warning: local playtime tracking could not start: {error}")
        exit_code = subprocess.call(
            [*wrapper, *arguments], cwd=game.game_dir, env=environment
        )
    except BaseException as error:
        launch_finished(paths, launch_id, started, error=str(error))
        raise
    finally:
        if playtime_session is not None:
            try:
                playtime_session.close()
            except OSError as error:
                print(f"warning: local playtime could not be saved: {error}")
    launch_finished(paths, launch_id, started, exit_code=exit_code)
    return exit_code
