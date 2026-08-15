from __future__ import annotations

import os
import json
import shlex
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import __version__
from .config import Game, Paths
from .diagnostics import (
    clear_runtime_traces,
    collect_game_logs,
    finish_launch_log,
    launch_finished,
    launch_started,
    prepare_debug_logs,
    prepare_launch_log,
    prepare_proton_logs,
    trim_runtime_traces,
    system_build_components,
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
    install_rift_runtime,
    launch_environment,
    native_xr_bridge,
    select_openvr_runtime,
    xr_build_components,
)
from .steam import ensure_steam_running
from .util import RiftLiftError, linux_to_windows

_DEBUG_ENVIRONMENT_KEYS = (
    "PROTON_LOG",
    "WINEDEBUG",
    "DXVK_LOG_LEVEL",
    "VKD3D_DEBUG",
    "VKD3D_SHADER_DEBUG",
    "VK_LOADER_DEBUG",
    "XR_LOADER_DEBUG",
    "RUST_LOG",
    "XRIZER_LOG_DIR",
    "XR_RUNTIME_JSON",
    "VR_OVERRIDE",
    "VR_PATHREG_OVERRIDE",
    "RIFTLIFT_XRIZER",
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


@contextmanager
def _steam_appid_marker(game: Game) -> Iterator[None]:
    """Prevent Steamworks from replacing RiftLift's prepared game process.

    Valve documents ``steam_appid.txt`` as the supported way to make
    ``SteamAPI_RestartAppIfNecessary`` keep a directly launched development
    process. RiftLift needs the same property: a Steam-client relaunch discards
    the selected XR runtime and diagnostic environment. Keep the marker only
    for the lifetime of the launch and never replace a file owned by the game.
    """
    if game.source != "steam" or not game.steam_app_id:
        yield
        return
    marker = game.game_dir / "steam_appid.txt"
    created: tuple[int, int] | None = None
    try:
        try:
            descriptor = os.open(
                marker,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o644,
            )
        except FileExistsError:
            descriptor = -1
        except OSError as error:
            raise RiftLiftError(
                f"cannot prepare Steamworks launch marker {marker}: {error}"
            ) from error
        if descriptor >= 0:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(f"{game.steam_app_id}\n".encode())
                stat = os.fstat(stream.fileno())
                created = (stat.st_dev, stat.st_ino)
        yield
    finally:
        if created is not None:
            try:
                stat = marker.stat()
                if (stat.st_dev, stat.st_ino) == created:
                    marker.unlink()
            except FileNotFoundError:
                pass


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


def _installed_openvr_build(path: Path, kind: str) -> str:
    if kind == "steamvr":
        try:
            build = (path / "bin/version.txt").read_text(errors="replace").strip()
        except OSError:
            build = ""
        return f"SteamVR {build[:160]}" if build else "SteamVR (build unknown)"
    installed = _installed_build(path)
    if installed == "missing" and str(path):
        return f"external-unversioned:{path.name}"
    return installed


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
    openvr_kind: str,
) -> dict[str, str]:
    if backend == "openvr":
        openvr_build = _installed_openvr_build(Path(openvr_runtime), openvr_kind)
    else:
        openvr_build = "not-used(openxr)"
    openvr_transport = {
        "steamvr": "SteamVR direct (no XRizer)",
        "xrizer": "bundled XRizer -> active OpenXR runtime",
        "external": "explicit external OpenVR runtime",
        "not-used": "not-used(openxr)",
    }.get(openvr_kind, openvr_kind)
    runtime_build = _installed_build(rift_runtime)
    return {
        "riftlift": __version__,
        "compat_runtime": runtime_build,
        "openvr_runtime": openvr_build,
        "openvr_transport": openvr_transport,
        "proton": _installed_proton_build(proton_root),
        **_installed_meta_builds(paths),
        "platform_bridge": f"compat-runtime:{runtime_build}",
        **system_build_components(),
        **xr_build_components(),
    }


def _expected_launch_components(
    components: dict[str, str], openvr_kind: str
) -> dict[str, str]:
    """Describe what this launch was expected to use without mislabeling Valve.

    SteamVR and explicitly selected external OpenVR runtimes are not RiftLift
    payloads. Their captured build is therefore the expected build for that
    launch; only the bundled XRizer runtime is pinned to RiftLift's release.
    """
    expected = dict(_EXPECTED_BUILD_COMPONENTS)
    if openvr_kind in {"steamvr", "external"}:
        expected["openvr_runtime"] = components["openvr_runtime"]
    return expected


def runtime_backend(game: Game) -> str:
    """Select a translation path from install capabilities, never a title list."""
    override = os.environ.get("RIFTLIFT_RUNTIME_BACKEND", "").strip().lower()
    if override:
        if override not in {"openxr", "openvr"}:
            raise RiftLiftError("RIFTLIFT_RUNTIME_BACKEND must be 'openxr' or 'openvr'")
        return override

    # Games shipping both Oculus and OpenVR integrations generally depend on
    # the mature OpenVR compositor behavior. D3D12 Oculus clients and engines
    # explicitly requesting their legacy OVR presentation mode also need that
    # path because the direct OpenXR bridge does not implement every legacy
    # compositor behavior. Other Oculus-only installs take the shorter OpenXR
    # path. These are capability probes, not titles.
    legacy_ovr_presentation = any(
        argument.casefold() in {"-ovr", "-vr_presentation"}
        for argument in game.arguments
    )
    needs_openvr = (
        uses_openvr_runtime(game.game_dir)
        or uses_oculus_xr_plugin(game.game_dir)
        or uses_d3d12_runtime(game.executable_path)
        or legacy_ovr_presentation
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


def _clear_stale_openvr_registry(paths: Paths, proton_root: Path) -> None:
    """Make Proton probe the OpenVR runtime selected for this launch.

    Proton caches its native OpenVR runtime and Vulkan-extension bridge in the
    shared Wine registry.  A Wine server first initialized without RiftLift's
    bundled XRizer can therefore keep pointing at SteamVR even after
    ``VR_OVERRIDE`` changes.  Removing only this generated cache makes the
    next Proton process rebuild it from the launch environment.
    """
    wine = proton_root / "files/bin/wine"
    if not wine.is_file():
        return
    environment = os.environ.copy()
    environment.update(
        {
            "WINEPREFIX": str(paths.prefix / "pfx"),
            "WINEDEBUG": "-all",
        }
    )
    # Host desktop injectors are unrelated to prefix maintenance and may not
    # have a matching 32-bit build, which only adds misleading loader errors.
    environment.pop("LD_PRELOAD", None)
    try:
        subprocess.run(
            [
                str(wine),
                "reg.exe",
                "delete",
                r"HKCU\Software\Wine\VR",
                "/f",
            ],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RiftLiftError(
            f"could not refresh Proton's OpenVR runtime cache: {error}"
        ) from error


def launch(paths: Paths, game: Game, extra_arguments: list[str]) -> int:
    if not game.executable_path.is_file():
        raise RiftLiftError(f"game executable is missing: {game.executable_path}")
    if game.source == "steam":
        ensure_steam_running()
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
    openvr_runtime = ""
    openvr_registry: Path | None = None
    openvr_kind = "not-used"
    if backend == "openvr":
        selected, openvr_registry, openvr_kind = select_openvr_runtime(paths)
        openvr_runtime = str(selected)
    environment = launch_environment(
        paths,
        game.game_dir,
        game.platform_shim,
        game.platform_offline or verified_rift_download,
    )
    if backend == "openvr" and openvr_kind != "xrizer":
        # Valve's native OpenVR client is the compositor connection. Loading
        # SteamVR's OpenXR client in the same Wine process gives vrclient.so a
        # second, conflicting shared-state lifetime; current SteamVR then
        # faults on ordinary device queries. XRizer is the only OpenVR target
        # here that needs WineOpenXR and the host OpenXR runtime alongside it.
        environment.pop("XR_RUNTIME_JSON", None)
        environment.pop("PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES", None)
        environment.pop("OXR_ZERO_TIME_IS_NOW", None)
        overrides = environment.get("WINEDLLOVERRIDES", "").strip(";")
        environment["WINEDLLOVERRIDES"] = (
            f"wineopenxr=d{';' + overrides if overrides else ''}"
        )
    if environment.get("PROTON_LOG") == "1":
        environment["RIFTLIFT_RUNTIME_TRACE"] = "1"
        # The bridge writes a compact first-call trace in Wine's temp folder.
        # Keep only the current reproduction so this cannot grow indefinitely
        # or make doctor correlate an old game's calls with a new failure.
        clear_runtime_traces(paths)
    if game.source == "steam" and game.steam_app_id:
        # Steam-distributed Oculus builds may still use Steamworks for DRM,
        # ownership, saves, or startup. Rift-store games deliberately keep the
        # isolated zero identity supplied by proton_environment. A Meta game
        # synchronized into Steam also has a shortcut app ID; that ID is not a
        # Steamworks identity and must never select Proton's steam.exe path.
        steam_id = str(game.steam_app_id)
        environment["SteamAppId"] = steam_id
        environment["SteamGameId"] = steam_id
        # GE-Proton's direct entry point normally starts its steam.exe shim
        # for a nonzero Steam identity. That shim asks the host client to
        # relaunch the title and loses RiftLift's selected XR runtime. UMU's
        # no-Steam entry point bypasses only that launcher shim; Proton's
        # lsteamclient and the real Steam identity remain available to games.
        environment["UMU_ID"] = f"umu-{steam_id}"
        environment["UMU_USE_STEAM"] = "0"
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
        # Keep the selected backend authoritative. Direct OpenVR must retain
        # Proton's VR Vulkan extensions for texture sharing with SteamVR or
        # XRizer; DXVK_NO_VR corrupts or suppresses that compositor path.
        environment["DXVK_NO_VR"] = "1"
    if backend == "openvr":
        action_manifest = rift_runtime / "Input" / "action_manifest.json"
        # XRizer consumes this value directly in its native Linux client, but
        # Valve's Proton bridge treats SetActionManifestPath as a Windows API
        # and converts its argument back to a host path. Passing Valve a host
        # path makes that conversion return null and can abort the entire game.
        environment["RIFTLIFT_ACTION_MANIFEST"] = (
            str(action_manifest)
            if openvr_kind == "xrizer"
            else linux_to_windows(action_manifest)
        )
    if backend == "openvr":
        # proton_environment intentionally removes inherited OpenVR state so
        # direct OpenXR launches cannot be polluted by it. The other bridge is
        # an OpenVR client, however, so preserve the caller's selected
        # OpenVR-to-OpenXR runtime (normally XRizer) for this backend only.
        environment["VR_OVERRIDE"] = openvr_runtime
        # Proton only consumes VR_OVERRIDE after it has loaded a valid path
        # registry. Point it at RiftLift's private registry so fresh systems do
        # not require SteamVR (or a pre-existing user OpenVR configuration).
        environment["VR_PATHREG_OVERRIDE"] = str(openvr_registry)
        if openvr_kind == "xrizer":
            environment["RIFTLIFT_XRIZER"] = "1"
        else:
            environment.pop("RIFTLIFT_XRIZER", None)
            environment.pop("XRIZER_LOG_DIR", None)
        _clear_stale_openvr_registry(paths, proton_root)
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
    launch_components = _launch_build_components(
        paths,
        proton_root,
        rift_runtime,
        openvr_runtime,
        backend,
        openvr_kind,
    )
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
        components=launch_components,
        expected_components=_expected_launch_components(launch_components, openvr_kind),
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
                    trim_runtime_traces(paths)
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
                with _steam_appid_marker(game):
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
        trim_runtime_traces(paths)
        if environment.get("PROTON_LOG", "0") != "0":
            collect_game_logs(
                paths,
                game,
                launch_id,
                time.time() - max(0.0, time.monotonic() - started),
            )
            prepare_debug_logs(paths)
        if playtime_session is not None:
            try:
                playtime_session.close()
            except OSError as error:
                print(f"warning: local playtime could not be saved: {error}")
    launch_finished(paths, launch_id, started, exit_code=exit_code)
    return exit_code
