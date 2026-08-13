from __future__ import annotations

import os
import json
import shlex
import shutil
import subprocess
import threading
from pathlib import Path

from . import __version__
from .config import Game, Paths
from .diagnostics import (
    finish_launch_log,
    launch_finished,
    launch_started,
    prepare_debug_logs,
    prepare_launch_log,
    prepare_proton_logs,
)
from .detection import (
    is_unity_player,
    is_unreal_shipping,
    uses_d3d12_runtime,
    uses_oculus_xr_plugin,
    uses_openvr_runtime,
)
from .playtime import PlaytimeSession
from .runtime import (
    META_CLIENT_COMPAT_MARKER,
    META_PACKAGES,
    OPENVR_RUNTIME_VERSION,
    PROTON_VERSION,
    RUNTIME_VERSION,
    install_proton,
    install_openvr_runtime,
    install_rift_runtime,
    launch_environment,
    native_xr_bridge,
    xr_build_components,
)
from .util import RiftLiftError, linux_to_windows

_DEBUG_ENVIRONMENT_KEYS = (
    "PROTON_LOG",
    "WINEDEBUG",
    "DXVK_LOG_LEVEL",
    "VKD3D_DEBUG",
    "VKD3D_SHADER_DEBUG",
    "VK_LOADER_DEBUG",
    "XR_LOADER_DEBUG",
    "XR_RUNTIME_JSON",
    "VR_OVERRIDE",
    "DXVK_NO_VR",
    "PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES",
    "OXR_ZERO_TIME_IS_NOW",
    "WINEDLLOVERRIDES",
    "SteamAppId",
    "UMU_ID",
    "UMU_USE_STEAM",
    "RIFTLIFT_RUNTIME_TRACE",
)

_EXPECTED_BUILD_COMPONENTS = {
    "riftlift": __version__,
    "compat_runtime": RUNTIME_VERSION,
    "openvr_runtime": OPENVR_RUNTIME_VERSION,
    "proton": PROTON_VERSION,
    **{
        f"meta_{package.name.replace('-', '_')}": f"205.0 sha256:{package.sha256[:12]}"
        for package in META_PACKAGES
    },
    "meta_client_patch": META_CLIENT_COMPAT_MARKER,
    "platform_bridge": f"compat-runtime:{RUNTIME_VERSION}",
}


def _installed_build(path: Path, marker: str = ".riftlift-version") -> str:
    try:
        value = (path / marker).read_text(errors="replace").strip()
    except OSError:
        return "missing"
    return value[:160] or "unknown"


def _installed_proton_build(path: Path) -> str:
    if not (path / "proton").is_file():
        return "missing"
    for relative in ("version", "files/version"):
        try:
            value = (path / relative).read_text(errors="replace").strip()
        except OSError:
            continue
        if value:
            return value[:160]
    return path.name


def _installed_meta_builds(paths: Paths) -> dict[str, str]:
    support = paths.prefix / "pfx/drive_c/Program Files/Oculus/Support"
    result: dict[str, str] = {}
    for package in META_PACKAGES:
        marker = support / package.name / ".riftlift-package.json"
        try:
            sha256 = str(json.loads(marker.read_text()).get("sha256", ""))
        except (OSError, json.JSONDecodeError):
            sha256 = ""
        result[f"meta_{package.name.replace('-', '_')}"] = (
            f"205.0 sha256:{sha256[:12]}" if sha256 else "missing/unknown"
        )
    patch = support / "oculus-client" / META_CLIENT_COMPAT_MARKER
    result["meta_client_patch"] = (
        META_CLIENT_COMPAT_MARKER if patch.is_file() else "missing"
    )
    return result


def _launch_build_components(
    paths: Paths,
    proton_root: Path,
    rift_runtime: Path,
    openvr_runtime: str,
    backend: str,
) -> dict[str, str]:
    if backend == "openvr":
        openvr_build = _installed_build(Path(openvr_runtime))
        if openvr_build == "missing" and openvr_runtime:
            openvr_build = f"external-unversioned:{Path(openvr_runtime).name}"
    else:
        openvr_build = "not-used(openxr)"
    runtime_build = _installed_build(rift_runtime)
    return {
        "riftlift": __version__,
        "compat_runtime": runtime_build,
        "openvr_runtime": openvr_build,
        "proton": _installed_proton_build(proton_root),
        **_installed_meta_builds(paths),
        "platform_bridge": f"compat-runtime:{runtime_build}",
        **xr_build_components(),
    }


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
    if environment.get("PROTON_LOG") == "1":
        environment["RIFTLIFT_RUNTIME_TRACE"] = "1"
        # The bridge writes a compact first-call trace in Wine's temp folder.
        # Keep only the current reproduction so this cannot grow indefinitely
        # or make doctor correlate an old game's calls with a new failure.
        for trace in (paths.prefix / "pfx/drive_c/users").glob(
            "*/Temp/riftlift-runtime-trace.log"
        ):
            trace.unlink(missing_ok=True)
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
        debug_logging=environment.get("PROTON_LOG", "0") != "0",
        debug_settings={
            key: environment[key]
            for key in _DEBUG_ENVIRONMENT_KEYS
            if key in environment and environment.get("PROTON_LOG", "0") != "0"
        },
        components=_launch_build_components(
            paths, proton_root, rift_runtime, openvr_runtime, backend
        ),
        expected_components=_EXPECTED_BUILD_COMPONENTS,
    )
    print(
        "Native XR bridge: "
        f"{native_bridge.pe.name} -> {native_bridge.unix.name} (Wine unixlib)"
    )
    log_path = prepare_launch_log(paths, launch_id)
    print(f"Launch log: {log_path}")
    playtime_session: PlaytimeSession | None = None
    try:
        try:
            playtime_session = PlaytimeSession(paths, game.slug)
        except OSError as error:
            print(f"warning: local playtime tracking could not start: {error}")
        descriptor = os.open(
            log_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as launch_log:
            stop_log_maintenance = threading.Event()

            def maintain_log_limits() -> None:
                while not stop_log_maintenance.wait(1):
                    finish_launch_log(log_path)
                    # These two streams are opened in append mode, so they can
                    # be compacted safely while the game runs. DXVK and crash
                    # writers may use positional writes; compact those only
                    # after the child exits to avoid corrupting useful output.
                    prepare_proton_logs(paths)

            maintenance = None
            if environment.get("PROTON_LOG", "0") != "0":
                maintenance = threading.Thread(
                    target=maintain_log_limits,
                    daemon=True,
                    name="riftlift-log-maintenance",
                )
                maintenance.start()
            try:
                exit_code = subprocess.call(
                    [*wrapper, *arguments],
                    cwd=game.game_dir,
                    env=environment,
                    stdout=launch_log,
                    stderr=subprocess.STDOUT,
                )
            finally:
                stop_log_maintenance.set()
                if maintenance is not None:
                    maintenance.join(timeout=2)
    except BaseException as error:
        launch_finished(
            paths,
            launch_id,
            started,
            error=str(error).strip() or type(error).__name__,
        )
        raise
    finally:
        finish_launch_log(log_path)
        if environment.get("PROTON_LOG", "0") != "0":
            prepare_debug_logs(paths)
        if playtime_session is not None:
            try:
                playtime_session.close()
            except OSError as error:
                print(f"warning: local playtime could not be saved: {error}")
    launch_finished(paths, launch_id, started, exit_code=exit_code)
    return exit_code
