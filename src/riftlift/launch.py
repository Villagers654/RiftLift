from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .config import Game, Paths
from .detection import (
    is_unity_player,
    is_unreal_shipping,
    uses_d3d12_runtime,
    uses_oculus_xr_plugin,
    uses_openvr_runtime,
)
from .diagnostics import (
    clear_runtime_traces,
    collect_game_logs,
    finish_launch_log,
    launch_finished,
    launch_started,
    prepare_debug_logs,
    prepare_launch_log,
    prepare_proton_logs,
    system_build_components,
    trim_runtime_traces,
)
from .playtime import PlaytimeSession
from .runtime import (
    DXVK_SHA256,
    DXVK_VERSION,
    META_PACKAGES,
    META_VERSION,
    OPENVR_RUNTIME_VERSION,
    PROTON_VERSION,
    RUNTIME_VERSION,
    NativeXrBridge,
    install_proton,
    install_rift_runtime,
    launch_environment,
    native_xr_bridge,
    select_openvr_runtime,
    steamvr_runtime_for_openxr,
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
    "RIFTLIFT_LAUNCH_ID",
)

_EXPECTED_BUILD_COMPONENTS = {
    "riftlift": __version__,
    "compat_runtime": RUNTIME_VERSION,
    "openvr_runtime": OPENVR_RUNTIME_VERSION,
    "proton": PROTON_VERSION,
    "dxvk": f"{DXVK_VERSION} sha256:{DXVK_SHA256[:12]}",
    **{
        f"meta_{package.name.replace('-', '_')}": f"{META_VERSION} sha256:{package.sha256[:12]}"
        for package in META_PACKAGES
    },
    "platform_bridge": f"compat-runtime:{RUNTIME_VERSION}",
}


def _marked_launch_processes(launch_id: str) -> list[int]:
    marker = f"RIFTLIFT_LAUNCH_ID={launch_id}".encode()
    result: list[int] = []
    for target in Path("/proc").iterdir():
        if not target.name.isdigit():
            continue
        try:
            environment = (target / "environ").read_bytes().split(b"\0")
        except (OSError, PermissionError):
            continue
        if marker in environment:
            result.append(int(target.name))
    return result


def _run_game_process(
    arguments: list[str], *, launch_id: str, **options: object
) -> int:
    """Run the Proton launcher and tear down its whole tree on cancellation."""
    process = subprocess.Popen(arguments, start_new_session=True, **options)
    try:
        return process.wait()
    except BaseException:
        if process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        # Detached Wine children retain the launch marker after leaving Proton's group.
        remaining = _marked_launch_processes(launch_id)
        for pid in remaining:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while remaining and time.monotonic() < deadline:
            time.sleep(0.05)
            remaining = _marked_launch_processes(launch_id)
        for pid in remaining:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        raise


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


def _installed_dxvk_build(path: Path) -> str:
    marker = path / "files/lib/wine/dxvk/.riftlift-dxvk.json"
    try:
        payload = json.loads(marker.read_text())
        version = str(payload.get("version", "")).strip()
        artifact = str(payload.get("artifact_sha256", "")).strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        return "missing"
    if not version:
        return "unknown"
    return f"{version[:120]} sha256:{artifact[:12] or 'unknown'}"


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
            f"{META_VERSION} sha256:{sha256[:12]}" if sha256 else "missing/unknown"
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
        "dxvk": _installed_dxvk_build(proton_root),
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

    # The direct bridge does not implement D3D12 or every legacy OVR presentation path.
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
        # Unreal needs both stereo rendering and an explicit Oculus plugin selection.
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
        # Replace any inherited Unity runtime selector instead of stacking one.
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
    # Prefix maintenance may invoke 32-bit Wine without a matching host injector.
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


def _configure_steamvr_openxr_fallback(
    paths: Paths, backend: str, environment: dict[str, str]
) -> None:
    """Mirror SteamVR's private OpenVR registry for its OpenXR native client."""
    if backend != "openxr" or not environment.get("XR_RUNTIME_JSON"):
        return
    manifest = Path(environment["XR_RUNTIME_JSON"])
    if steamvr_runtime_for_openxr(manifest) is None:
        return
    _, registry, _ = select_openvr_runtime(paths, manifest)
    environment["VR_PATHREG_OVERRIDE"] = str(registry)
    environment["XDG_CONFIG_HOME"] = str(paths.config)


def _disable_openxr_for_direct_openvr(
    environment: dict[str, str], openvr_kind: str
) -> None:
    """Prevent two conflicting native compositor clients in one Wine process."""
    if openvr_kind == "xrizer":
        return
    environment.pop("XR_RUNTIME_JSON", None)
    environment.pop("PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES", None)
    environment.pop("OXR_ZERO_TIME_IS_NOW", None)
    overrides = environment.get("WINEDLLOVERRIDES", "").strip(";")
    environment["WINEDLLOVERRIDES"] = (
        f"wineopenxr=d{';' + overrides if overrides else ''}"
    )


def _configure_proton_identity(environment: dict[str, str], game: Game) -> None:
    """Select a direct GE-Proton/UMU identity without a Steam client relaunch."""
    if game.source == "steam" and game.steam_app_id:
        steam_id = str(game.steam_app_id)
        environment["SteamAppId"] = steam_id
        environment["SteamGameId"] = steam_id
        environment["UMU_ID"] = f"umu-{steam_id}"
    else:
        environment["UMU_ID"] = "umu-default"
    environment["UMU_USE_STEAM"] = "0"


def _configure_openvr_environment(
    paths: Paths,
    environment: dict[str, str],
    rift_runtime: Path,
    proton_root: Path,
    openvr_runtime: str,
    openvr_registry: Path,
    openvr_kind: str,
) -> None:
    """Apply environment that is meaningful only to an OpenVR launch."""
    action_manifest = rift_runtime / "Input/action_manifest.json"
    environment["RIFTLIFT_ACTION_MANIFEST"] = (
        str(action_manifest)
        if openvr_kind == "xrizer"
        else linux_to_windows(action_manifest)
    )
    environment["VR_OVERRIDE"] = openvr_runtime
    environment["VR_PATHREG_OVERRIDE"] = str(openvr_registry)
    environment["XDG_CONFIG_HOME"] = str(paths.config)
    if openvr_kind == "xrizer":
        environment["RIFTLIFT_XRIZER"] = "1"
    else:
        environment.pop("RIFTLIFT_XRIZER", None)
        environment.pop("XRIZER_LOG_DIR", None)
    _clear_stale_openvr_registry(paths, proton_root)


def _launch_wrapper() -> list[str]:
    value = os.environ.get("RIFTLIFT_LAUNCH_WRAPPER", "").strip()
    if not value:
        return []
    wrapper = shlex.split(value)
    if not wrapper or not shutil.which(wrapper[0]):
        raise RiftLiftError(f"configured launch wrapper was not found: {value}")
    return wrapper


def _game_capabilities(game: Game) -> list[str]:
    return [
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


@dataclass(slots=True)
class _LaunchPlan:
    arguments: list[str]
    environment: dict[str, str]
    wrapper: list[str]
    backend: str
    openvr_runtime: str
    openvr_kind: str
    proton_root: Path
    rift_runtime: Path
    native_bridge: NativeXrBridge


def _prepare_launch(
    paths: Paths, game: Game, extra_arguments: list[str]
) -> _LaunchPlan:
    if not game.executable_path.is_file():
        raise RiftLiftError(f"game executable is missing: {game.executable_path}")
    if game.source == "steam":
        ensure_steam_running()
    rift_runtime = install_rift_runtime(paths)
    proton_root = install_proton(paths)
    backend = runtime_backend(game)
    native_bridge = native_xr_bridge(proton_root, backend)
    arguments = [
        str(proton_root / "proton"),
        "run",
        str(rift_runtime / "RiftLiftLauncher.exe"),
        "/wait",
        f"/{backend}",
        "/app",
        game.app_key,
        "/cwd",
        linux_to_windows(game.game_dir),
        linux_to_windows(game.executable_path),
        *oculus_launch_arguments(game, extra_arguments),
    ]
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
        game.platform_offline or game.source == "meta",
    )
    _configure_steamvr_openxr_fallback(paths, backend, environment)
    if backend == "openvr":
        _disable_openxr_for_direct_openvr(environment, openvr_kind)
    if environment.get("PROTON_LOG") == "1":
        environment["RIFTLIFT_RUNTIME_TRACE"] = "1"
        clear_runtime_traces(paths)
    _configure_proton_identity(environment, game)
    if backend == "openxr":
        environment["DXVK_NO_VR"] = "1"
    elif openvr_registry is not None:
        _configure_openvr_environment(
            paths,
            environment,
            rift_runtime,
            proton_root,
            openvr_runtime,
            openvr_registry,
            openvr_kind,
        )
    else:
        raise RiftLiftError("OpenVR launch has no selected path registry")
    return _LaunchPlan(
        arguments,
        environment,
        _launch_wrapper(),
        backend,
        openvr_runtime,
        openvr_kind,
        proton_root,
        rift_runtime,
        native_bridge,
    )


def _start_launch_record(
    paths: Paths, game: Game, plan: _LaunchPlan
) -> tuple[str, float, Path]:
    print(
        f"Launching {game.name} through "
        f"RiftLift native {plan.backend.upper()} runtime -> headset..."
    )
    components = _launch_build_components(
        paths,
        plan.proton_root,
        plan.rift_runtime,
        plan.openvr_runtime,
        plan.backend,
        plan.openvr_kind,
    )
    debug_logging = plan.environment.get("PROTON_LOG", "0") != "0"
    launch_id, started = launch_started(
        paths,
        game,
        plan.backend,
        wrapper=bool(plan.wrapper),
        capabilities=_game_capabilities(game),
        debug_logging=debug_logging,
        debug_settings={
            key: plan.environment[key]
            for key in _DEBUG_ENVIRONMENT_KEYS
            if key in plan.environment and debug_logging
        },
        components=components,
        expected_components=_expected_launch_components(components, plan.openvr_kind),
    )
    plan.environment["RIFTLIFT_LAUNCH_ID"] = launch_id
    print(
        "Native XR bridge: "
        f"{plan.native_bridge.pe.name} -> {plan.native_bridge.unix.name} (Wine unixlib)"
    )
    log_path = prepare_launch_log(paths, launch_id)
    print(f"Launch log: {log_path}")
    return launch_id, started, log_path


def launch(paths: Paths, game: Game, extra_arguments: list[str]) -> int:
    plan = _prepare_launch(paths, game, extra_arguments)
    environment = plan.environment
    launch_id, started, log_path = _start_launch_record(paths, game, plan)
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
                    # Only append-mode streams are safe to compact while writers run.
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
                    exit_code = _run_game_process(
                        [*plan.wrapper, *plan.arguments],
                        launch_id=launch_id,
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
