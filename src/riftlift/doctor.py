from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, doctor_components, doctor_system
from .auth import is_signed_in
from .config import Game, Paths, games
from .detection import (
    is_unity_player,
    is_unreal_shipping,
    uses_d3d12_runtime,
    uses_oculus_xr_plugin,
    uses_openvr_runtime,
)
from .diagnostics import (
    recent_launches,
    redact,
    utc_now,
)
from .doctor_evidence import (
    _cancelled_launch,
    _capped_journal_until,
    _failed_launch,
    _recent_coredumps,
    _recent_debug_file_errors,
    _recent_envision_log_errors,
    _recent_game_log_errors,
    _recent_journal_errors,
    _recent_kernel_errors,
    _recent_launch_log_errors,
    _recent_proton_log_errors,
    _recent_steam_log_errors,
)
from .launch import runtime_backend
from .runtime import (
    META_PACKAGES,
    META_RUNTIME_SIGNED_FILES,
    META_SIGNING_ROOT_THUMBPRINT,
    META_VERSION,
    debug_logging_active,
    meta_signing_root_installed,
    native_xr_bridge,
    proton_dir,
    steamvr_runtime_for_openxr,
)
from .steam import steam_root
from .util import RiftLiftError
from .xr_runtime import active_runtime_json

PASTE_URL = "https://paste.rs/"
_MAX_REPORT = 48 * 1024

# Keep these private aliases local so report assembly remains easy to patch in
# focused tests while their implementations live with the data they inspect.
_component_comparison = doctor_components.component_comparison
_component_matches = doctor_components.component_matches
_current_components = doctor_components.current_components
_expected_components = doctor_components.expected_components
_file_identity = doctor_components.file_identity
_installed_dxvk = doctor_components.installed_dxvk
_proton_version = doctor_components.proton_version
_connected_inputs = doctor_system.connected_inputs
_cpu_name = doctor_system.cpu_name
_gpu_summary = doctor_system.gpu_summary
_memory = doctor_system.memory
_os_name = doctor_system.os_name
_relevant_processes = doctor_system.relevant_processes
_runtime_description = doctor_system.runtime_description
_service_state = doctor_system.service_state


_CHECK_RECOMMENDATIONS = (
    (
        {"Active OpenXR runtime"},
        "Configure a working OpenXR runtime, then rerun `riftlift doctor`.",
    ),
    ({"Steam"}, "Start and sign in to Steam once so RiftLift can find it."),
    (
        {
            "GE-Proton",
            "RiftLift DXVK compatibility",
            "Windows ABI launcher",
            "OpenXR ABI bridge",
            "OpenVR ABI bridge",
            "Native OPENXR unixlib",
            "Native OPENVR unixlib",
            "RiftLift OpenVR translator",
            "Meta client",
            "Platform bridge",
        },
        "Run `riftlift setup` to repair missing compatibility components.",
    ),
    ({"Meta sign-in"}, "Run `riftlift login` before installing Meta-owned games."),
)

_EVIDENCE_RECOMMENDATIONS = (
    (
        ("xr_error_api_version_unsupported",),
        "Update RiftLift and the selected OpenXR runtime, then run `riftlift setup`.",
    ),
    (
        (
            "xr_error_runtime_unavailable",
            "openxr result -51",
            "xrcreateinstance failed: -51",
        ),
        "Start the selected OpenXR runtime service and retry.",
    ),
    (
        ("vk_error_device_lost", "gpu reset", "ring timeout", "vm fault"),
        "The Vulkan device was lost. Inspect the kernel journal, restart the XR "
        "runtime, and retry without graphics overlays.",
    ),
    (
        ("xr_error_form_factor_unavailable",),
        "Connect the headset and start an active HMD session before retrying.",
    ),
    (
        ("xr_error_graphics_device_invalid",),
        "Ensure the game and OpenXR runtime select the same GPU, then retry.",
    ),
    (
        ("out_of_device_memory", "out of device memory"),
        "Close GPU-heavy applications, reduce VR resolution, and retry.",
    ),
)


def _recommendations(
    checks: list[tuple[str, bool, str]],
    launches: list[dict[str, object]],
    debug_logging: bool,
    evidence: list[str],
    current_components: dict[str, str],
) -> list[str]:
    failed_labels = {label for label, ok, _detail in checks if not ok}
    result = [
        message for labels, message in _CHECK_RECOMMENDATIONS if failed_labels & labels
    ]
    if any(label.startswith("Game: ") for label in failed_labels):
        result.append("Repair or re-register games whose executable is marked missing.")
    unsuccessful = [item for item in launches if _failed_launch(item)]
    expected = _expected_components()
    stale_now = [
        name
        for name, version in expected.items()
        if not _component_matches(
            name, current_components.get(name, "unknown"), version
        )
    ]
    if stale_now:
        result.append(
            "Run `riftlift setup` with this RiftLift build: currently installed "
            "component versions do not match it (" + ", ".join(stale_now) + ")."
        )
    if unsuccessful:
        captured = unsuccessful[0].get("components")
        if not isinstance(captured, dict):
            result.append(
                "Reproduce with the current RiftLift build; this failed launch lacks "
                "a complete build snapshot."
            )
        elif any(
            not str(captured.get(name, "unknown")).startswith("not-used(")
            and str(captured.get(name, "unknown")) != current_components.get(name)
            for name in expected
        ):
            result.append(
                "Reproduce once with the current installed builds; the newest failed "
                "launch was captured with different component versions."
            )
    evidence_text = "\n".join(evidence).casefold()
    result.extend(
        message
        for signatures, message in _EVIDENCE_RECOMMENDATIONS
        if any(signature in evidence_text for signature in signatures)
    )
    if not launches:
        if not debug_logging:
            result.append(
                "Enable Debug logging in the RiftLift GUI before reproducing the "
                "problem."
            )
        result.append(
            "Launch the affected game through RiftLift, then rerun doctor to capture "
            "launch-correlated evidence."
        )
    elif unsuccessful and not all(item.get("debug_logging") for item in unsuccessful):
        if debug_logging:
            result.append(
                "Debug logging is now enabled; reproduce once more, then run System "
                "again."
            )
        else:
            result.append(
                "Enable Debug logging in the RiftLift GUI, reproduce once, then run "
                "System again."
            )
    return result


def _debug_capture_summary(paths: Paths, enabled: bool) -> list[str]:
    lines = [
        "Profile: "
        + (
            "Proton + Wine XR/Steam/Vulkan + OpenVR runtime + DXVK debug + "
            "VKD3D info + Vulkan/OpenXR loader + crash reports"
            if enabled
            else "disabled"
        )
    ]
    for label, name in (
        ("Proton", "proton"),
        ("Graphics", "graphics"),
        ("Crash", "crashes"),
        ("OpenVR runtime", "openvr"),
        ("Game", "game"),
        ("Launch", "logs"),
    ):
        directory = paths.data / "diagnostics" / name
        try:
            files = [item for item in directory.iterdir() if item.is_file()]
            size = sum(item.stat().st_size for item in files)
            newest = max((item.stat().st_mtime for item in files), default=None)
        except OSError:
            files, size, newest = [], 0, None
        newest_text = (
            datetime.fromtimestamp(newest, timezone.utc).isoformat(timespec="seconds")
            if newest is not None
            else "none"
        )
        lines.append(
            f"{label} files: {len(files)} retained, {size / 1024 / 1024:.1f} MiB; "
            f"newest={newest_text}"
        )
    lines.append(
        "Retention ceiling: approximately 165 MiB; oversized text keeps its header "
        "and failure tail."
    )
    lines.append(
        "Doctor also checks game and Steam/XR logs, user and kernel journals, "
        "relevant processes, coredump metadata, runtime services, and graphics/XR "
        "environment overrides."
    )
    return lines


_CAUSE_RULES = (
    (
        ("riftlift: patched 0 executable runtime imports",),
        "High confidence: RiftLift loaded but could not intercept the game's Oculus "
        "runtime imports.",
    ),
    (
        ("xr_error_api_version_unsupported",),
        "High confidence: the selected OpenXR runtime rejected the API version "
        "requested by the game. Check the build comparison and update the stale "
        "component.",
    ),
    (
        (
            "xr_error_runtime_unavailable",
            "openxr result -51",
            "xrcreateinstance failed: -51",
        ),
        "High confidence: the selected OpenXR runtime service was unavailable when "
        "the game initialized XR.",
    ),
    (
        ("failed to inject", "failed to create process"),
        "High confidence: the RiftLift launcher could not start or inject the "
        "compatibility bridge.",
    ),
    (
        ("gpu reset", "ring timeout", "vm fault", "illegal opcode in command stream"),
        "High confidence: the kernel recorded an AMD GPU command-stream hang, reset, "
        "or memory fault during the launch window.",
    ),
    (
        ("vk_error_device_lost", "device lost"),
        "Strong lead: DXVK/Vulkan lost the logical GPU device. Check the kernel "
        "journal to distinguish a driver reset from a userspace graphics failure.",
    ),
    (
        ("xr_error_form_factor_unavailable",),
        "High confidence: the OpenXR runtime did not have an available headset.",
    ),
    (
        ("xr_error_graphics_device_invalid",),
        "High confidence: the game and OpenXR runtime selected incompatible graphics "
        "devices.",
    ),
    (
        ("out_of_device_memory", "out of device memory"),
        "Strong lead: the Vulkan graphics device exhausted available memory.",
    ),
)


def _likely_cause(evidence: list[str], launches: list[dict[str, object]]) -> list[str]:
    joined = "\n".join(evidence).casefold()
    matched = next(
        (
            message
            for signatures, message in _CAUSE_RULES
            if any(signature in joined for signature in signatures)
        ),
        None,
    )
    if matched is not None:
        return [matched]
    vr_initialization_failed = bool(
        re.search(
            r"failed to initialize.{0,100}(?:oculus|ovr|openxr|vr (?:api|library|runtime|session))"
            r"|failed to initialize (?:oculus|ovr|openxr|vr)",
            joined,
        )
    )
    if vr_initialization_failed:
        bridge_loaded = bool(re.search(r"riftlift: patched [1-9]\d*", joined))
        detail = (
            "RiftLift loaded and intercepted the executable, but "
            if bridge_loaded
            else "The game started, but "
        )
        cause = (
            "High confidence: "
            + detail
            + "the game reported that VR runtime initialization failed. Treat "
            "that initialization error as primary; a later access violation or "
            "crash reporter is likely secondary. The selected evidence below "
            "preserves the game's original error text."
        )
    elif "coredump" in joined or "segfault" in joined or "fatal" in joined:
        cause = "Strong lead: a relevant process crashed during the launch window."
    elif "xr_error" in joined or ("openxr" in joined and "failed" in joined):
        cause = "Strong lead: OpenXR runtime or session initialization failed."
    elif "not found" in joined or "failed to load" in joined:
        cause = "Strong lead: a required runtime module or game file failed to load."
    elif any(
        not _cancelled_launch(item)
        and (item.get("event") != "finished" or item.get("exit_code") is None)
        for item in launches
    ):
        cause = (
            "Launch state is incomplete: RiftLift has no completion record yet. The "
            "game may still be running, or RiftLift was terminated during the launch."
        )
    elif evidence:
        cause = (
            "No single signature is decisive; the most relevant correlated errors "
            "are listed below."
        )
    else:
        cause = "No correlated failure signature was found in the retained sources."
    return [cause]


Check = tuple[str, bool, str]


@dataclass(slots=True)
class ComponentState:
    current: dict[str, str]
    expected: dict[str, str]
    cached_vulkan: str | None = None


def _record_check(
    checks: list[Check], label: str, action: Callable[[], object]
) -> None:
    try:
        value = action()
    except Exception as error:
        checks.append((label, False, redact(str(error))))
    else:
        checks.append((label, True, redact(str(value))))


def _component_state(
    paths: Paths,
    launches: list[dict[str, object]],
) -> ComponentState:
    current = _current_components(paths)
    expected = _expected_components()
    cached_vulkan = None
    captured = launches[0].get("components") if launches else None
    if not isinstance(captured, dict):
        return ComponentState(current, expected)
    vulkan = captured.get("system_vulkan")
    if isinstance(vulkan, str) and vulkan not in {"", "unavailable"}:
        cached_vulkan = vulkan
        current["system_vulkan"] = vulkan
    envision = captured.get("envision")
    if (
        current.get("envision") == "not installed/unknown"
        and isinstance(envision, str)
        and envision
    ):
        current["envision"] = envision
    return ComponentState(current, expected, cached_vulkan)


def _runtime_checks(paths: Paths, installed: list[Game]) -> list[Check]:
    checks: list[Check] = []
    runtime_ok, runtime_detail = _runtime_description()
    checks.append(("Active OpenXR runtime", runtime_ok, runtime_detail))
    _record_check(checks, "Steam", steam_root)

    try:
        proton = proton_dir()
    except RiftLiftError as error:
        proton = None
        checks.append(("GE-Proton", False, redact(str(error))))
        dxvk_ok, dxvk_detail = False, str(error)
    else:
        _record_check(
            checks,
            "GE-Proton",
            lambda: _proton_description(proton),
        )
        dxvk_ok, dxvk_detail = _installed_dxvk(proton)
    checks.append(("RiftLift DXVK compatibility", dxvk_ok, dxvk_detail))

    rift_runtime = paths.tools / "rift-runtime"
    for label, relative in (
        ("Windows ABI launcher", "RiftLiftLauncher.exe"),
        ("OpenXR ABI bridge", "RiftLiftOpenXR64.dll"),
        ("OpenVR ABI bridge", "RiftLiftOpenVR64.dll"),
    ):
        identity = _file_identity(rift_runtime / relative)
        checks.append((label, not identity.startswith("missing"), identity))

    required_backends = {
        backend
        for game in installed
        for backend in [_safe_runtime_backend(game)]
        if backend is not None
    }
    backends = ["openxr"]
    if "openvr" in required_backends:
        backends.append("openvr")
    checks.extend(_native_bridge_checks(proton, backends))
    if "openvr" in required_backends:
        checks.extend(_openvr_checks(paths))
    return checks


def _native_bridge_checks(proton: Path | None, backends: list[str]) -> list[Check]:
    checks: list[Check] = []
    for backend in backends:
        if proton is None:
            checks.append(
                (f"Native {backend.upper()} unixlib", False, "GE-Proton unavailable")
            )
            continue
        try:
            bridge = native_xr_bridge(proton, backend)
            detail = f"{_file_identity(bridge.pe)} + {_file_identity(bridge.unix)}"
        except RiftLiftError as error:
            checks.append((f"Native {backend.upper()} unixlib", False, str(error)))
        else:
            checks.append((f"Native {backend.upper()} unixlib", True, detail))
    return checks


def _proton_description(proton: Path) -> str:
    if not (proton / "proton").is_file():
        raise FileNotFoundError("not installed")
    return f"{redact(str(proton))} ({_proton_version(proton)})"


def _safe_runtime_backend(game: Game) -> str | None:
    with contextlib.suppress(RiftLiftError):
        return runtime_backend(game)
    return None


def _openvr_checks(paths: Paths) -> list[Check]:
    try:
        steamvr_runtime = steamvr_runtime_for_openxr(active_runtime_json())
    except RiftLiftError:
        steamvr_runtime = None
    if steamvr_runtime is None:
        runtime = paths.tools / "openvr-runtime/libxrizer.so"
        label = "RiftLift OpenVR translator (XRizer)"
        expected_path = runtime.parent
    else:
        runtime = steamvr_runtime / "bin/linux64/vrclient.so"
        label = "SteamVR OpenVR client (direct; no XRizer)"
        expected_path = steamvr_runtime

    checks: list[Check] = [(label, runtime.is_file(), _file_identity(runtime))]
    registry_path = paths.config / "openvr/openvrpaths.vrpath"
    try:
        registry = json.loads(registry_path.read_text())
        runtime_paths = registry.get("runtime", [])
        valid = (
            registry.get("version") == 1
            and isinstance(runtime_paths, list)
            and str(expected_path) in runtime_paths
        )
        detail = f"{redact(str(registry_path))}; runtime={redact(str(runtime_paths))}"
    except (OSError, json.JSONDecodeError, AttributeError) as error:
        valid = False
        detail = f"{redact(str(registry_path))}: {error}"
    checks.append(("Selected OpenVR path registry", valid, detail))
    return checks


def _meta_checks(paths: Paths) -> list[Check]:
    support = paths.prefix / "pfx/drive_c/Program Files/Oculus/Support"
    checks: list[Check] = []
    runtime = support / "oculus-runtime"
    for name, expected in META_RUNTIME_SIGNED_FILES.items():
        target = runtime / name
        current = ""
        if target.is_file():
            with contextlib.suppress(OSError):
                current = hashlib.sha256(target.read_bytes()).hexdigest()
        checks.append(
            (
                f"Meta signed loader: {name}",
                current == expected,
                f"{_file_identity(target)}; expected sha256 {expected[:12]}",
            )
        )

    trust_present = meta_signing_root_installed(paths)
    checks.append(
        (
            "Meta signing root",
            trust_present,
            f"Wine root store={'present' if trust_present else 'missing'}; "
            f"expected={META_SIGNING_ROOT_THUMBPRINT}",
        )
    )
    platform_files = (
        paths.tools / "platform-compat/LibOVRPlatform64_1.dll",
        paths.tools / "platform-compat/LibOVRPlatformImpl64_1.dll",
        paths.tools / "platform-compat/LibOVRPlatformImpl64_1_real.dll",
    )
    checks.append(
        (
            "Platform bridge",
            all(path.is_file() for path in platform_files),
            f"{sum(path.is_file() for path in platform_files)}/{len(platform_files)} files",
        )
    )
    signed_in = is_signed_in(paths)
    checks.append(
        (
            "Meta sign-in",
            signed_in,
            "signed in (credential cached)" if signed_in else "signed out",
        )
    )
    return checks


def _game_checks(installed: list[Game]) -> tuple[list[Check], list[str]]:
    checks: list[Check] = []
    lines: list[str] = []
    for game in installed:
        present = game.executable_path.is_file()
        capabilities = [
            name
            for name, value in (
                ("OpenVR", uses_openvr_runtime(game.game_dir)),
                ("OculusXR", uses_oculus_xr_plugin(game.game_dir)),
                ("D3D12", uses_d3d12_runtime(game.executable_path)),
                ("Unity", is_unity_player(game.executable_path)),
                ("Unreal", is_unreal_shipping(game.executable_path)),
            )
            if value
        ]
        try:
            backend = runtime_backend(game)
        except RiftLiftError as error:
            backend = f"error: {error}"
        state = "OK" if present else "MISSING"
        markers = ", ".join(capabilities) or "no engine markers"
        lines.append(f"{state:7} {game.name} [{game.source}; {backend}; {markers}]")
        checks.append(
            (f"Game: {game.name}", present, redact(str(game.executable_path)))
        )
    return checks, lines


def _system_report_lines(
    paths: Paths,
    components: ComponentState,
    debug_logging: bool,
    processes: list[str],
) -> list[str]:
    environment_names = (
        "MANGOHUD",
        "DISABLE_MANGOHUD",
        "ENABLE_VKBASALT",
        "OBS_VKCAPTURE",
        "LD_PRELOAD",
        "DRI_PRIME",
        "AMD_VULKAN_ICD",
        "GAMESCOPE_WSI",
        "VK_INSTANCE_LAYERS",
        "VK_DRIVER_FILES",
        "VK_ICD_FILENAMES",
        "DXVK_FILTER_DEVICE_NAME",
        "VKD3D_FILTER_DEVICE_NAME",
    )
    component_lines = [
        f"{'OK' if _component_matches(name, components.current[name], expected) else 'MISMATCH':8} "
        f"{name}: installed={components.current[name]}; expected={expected}"
        for name, expected in components.expected.items()
    ]
    vulkan = (
        f"{components.cached_vulkan} (latest launch snapshot; active probe skipped)"
        if components.cached_vulkan
        else "active probe skipped; no launch snapshot available"
    )
    return [
        f"RiftLift doctor {__version__}",
        f"Generated: {utc_now()}",
        "Public report: credentials, email addresses, and home paths are redacted.",
        "",
        "[Build identity at doctor run]",
        f"Doctor build: RiftLift {__version__}",
        f"Doctor module: {redact(str(Path(__file__).resolve()))}",
        *component_lines,
        "",
        "[System]",
        f"OS: {_os_name()}",
        f"Kernel: {platform.release()} ({platform.machine()})",
        f"Desktop: {os.environ.get('XDG_CURRENT_DESKTOP', 'unknown')} / {os.environ.get('XDG_SESSION_TYPE', 'unknown')}",
        f"CPU: {_cpu_name()}",
        f"Memory: {_memory()}",
        f"GPU: {_gpu_summary()}",
        f"Vulkan: {vulkan}",
        f"Input devices: {_connected_inputs()}",
        "",
        "[XR services]",
        f"monado.service: {_service_state('monado.service')}",
        f"wivrn.service: {_service_state('wivrn.service')}",
        f"XR_RUNTIME_JSON: {redact(os.environ.get('XR_RUNTIME_JSON', '<unset>'))}",
        f"VR_OVERRIDE: {redact(os.environ.get('VR_OVERRIDE', '<unset>'))}",
        f"VR_PATHREG_OVERRIDE: {redact(os.environ.get('VR_PATHREG_OVERRIDE', '<unset>'))}",
        f"RIFTLIFT_XRIZER: {redact(os.environ.get('RIFTLIFT_XRIZER', '<unset>'))}",
        "Debug logging: "
        + ("enabled (expanded bounded capture)" if debug_logging else "disabled"),
        "",
        "[Debug capture]",
        *_debug_capture_summary(paths, debug_logging),
        "",
        "[Relevant processes at doctor start]",
        *(processes or ["none detected"]),
        "",
        "[Graphics/XR environment]",
        *[
            f"{name}={redact(os.environ.get(name, '<unset>'))}"
            for name in environment_names
        ],
    ]


def _launch_report_lines(launches: list[dict[str, object]]) -> list[str]:
    if not launches:
        return ["No structured launch history yet (new launches will appear here)."]
    lines: list[str] = []
    for item in launches:
        if item.get("event") != "finished":
            outcome = "INCOMPLETE (still running or no completion recorded)"
        elif _cancelled_launch(item):
            outcome = "CANCELLED by user"
        elif item.get("error"):
            outcome = f"ERROR: {item['error']}"
        elif item.get("exit_code") is None:
            outcome = "INTERRUPTED (outcome not recorded)"
        else:
            outcome = (
                f"exit {item.get('exit_code', '?')} after "
                f"{item.get('duration_seconds', '?')}s"
            )
        capabilities = ",".join(item.get("capabilities", [])) or "none"
        lines.append(
            f"{item.get('started_at', item.get('at', '?'))}  "
            f"{item.get('game', item.get('slug', '?'))}  "
            f"{item.get('backend', '?')}  {outcome}  caps={capabilities}  "
            f"debug={'on' if item.get('debug_logging') else 'off'}  "
            f"build={item.get('riftlift_version', 'unknown')}"
        )
        components = item.get("components")
        if isinstance(components, dict):
            lines.append(
                "  captured components: "
                + "; ".join(f"{key}={value}" for key, value in components.items())
            )
        expected = item.get("expected_components")
        if isinstance(expected, dict) and expected:
            lines.append(
                "  expected by launch build: "
                + "; ".join(f"{key}={value}" for key, value in expected.items())
            )
    return lines


def _debug_settings_lines(launches: list[dict[str, object]]) -> list[str]:
    launch = next(
        (
            item
            for item in launches
            if item.get("debug_logging") and item.get("debug_settings")
        ),
        None,
    )
    if launch is None or not isinstance(launch.get("debug_settings"), dict):
        return []
    settings = launch["debug_settings"]
    return [
        "",
        "[Most recent debug launch settings]",
        *[
            f"{key}={value}"
            for key, value in settings.items()
            if isinstance(key, str) and isinstance(value, str)
        ],
    ]


def _collect_evidence(
    paths: Paths,
    launches: list[dict[str, object]],
    doctor_started: float,
) -> tuple[list[str], str | None]:
    launch_times = [
        item.get("started_at", item.get("at"))
        for item in launches
        if isinstance(item.get("started_at", item.get("at")), str)
        and item.get("started_at", item.get("at"))
    ]
    journal_since = max(launch_times) if launch_times else None
    latest = launches[:1]
    evidence = [
        *_recent_launch_log_errors(paths, latest),
        *_recent_proton_log_errors(paths, latest),
        *_recent_debug_file_errors(paths, latest, "graphics"),
        *_recent_debug_file_errors(paths, latest, "openvr", include_tail=True),
        *_recent_debug_file_errors(paths, latest, "game"),
        *_recent_debug_file_errors(paths, latest, "crashes", include_tail=True),
        *_recent_journal_errors(journal_since),
        *_recent_kernel_errors(journal_since),
        *_recent_coredumps(journal_since),
        *_recent_steam_log_errors(paths, latest),
        *_recent_game_log_errors(paths, latest),
        *_recent_envision_log_errors(latest, doctor_started),
    ]
    return evidence, journal_since


def _summary_lines(
    checks: list[Check],
    launches: list[dict[str, object]],
    current: dict[str, str],
    expected: dict[str, str],
) -> tuple[list[str], list[str]]:
    passed = sum(ok for _, ok, _ in checks)
    failed = len(checks) - passed
    cancelled = sum(_cancelled_launch(item) for item in launches)
    unsuccessful = sum(_failed_launch(item) for item in launches)
    successful = len(launches) - unsuccessful - cancelled
    stale = [
        name
        for name, expected_version in expected.items()
        if not _component_matches(name, current[name], expected_version)
    ]
    return (
        [
            "",
            f"[Summary] checks: {passed} passed, {failed} failed; "
            f"component builds: {len(stale)} mismatched; "
            f"shown launches: {successful} successful, "
            f"{cancelled} cancelled, {unsuccessful} failed/incomplete",
        ],
        stale,
    )


def _evidence_lines(
    evidence: list[str], launches: list[dict[str, object]]
) -> list[str]:
    if evidence:
        content = evidence
    elif launches:
        content = ["No matching errors found during the recorded launch window."]
    else:
        content = [
            "Journal scan skipped: no RiftLift launch window exists to distinguish "
            "game failures from unrelated Steam OpenXR probes."
        ]
    return ["", "[Recent error evidence]", *content]


def _bounded_report(lines: list[str]) -> str:
    report = redact("\n".join(lines).strip() + "\n")
    if len(report.encode()) <= _MAX_REPORT:
        return report
    encoded = report.encode()[: _MAX_REPORT - 100]
    return encoded.decode(errors="ignore") + "\n[report truncated]\n"


def build_report(paths: Paths) -> tuple[str, bool]:
    doctor_started = time.time()
    processes_at_start = _relevant_processes()
    installed = games(paths)
    launches = recent_launches(paths)
    components = _component_state(paths, launches)
    current_components = components.current
    expected_components = components.expected
    debug_logging = debug_logging_active(paths)
    game_checks, game_lines = _game_checks(installed)
    checks = [*_runtime_checks(paths, installed), *_meta_checks(paths), *game_checks]
    width = max(len(label) for label, _, _ in checks)
    lines = _system_report_lines(
        paths,
        components,
        debug_logging,
        processes_at_start,
    )
    lines.extend(["", "[Core checks]"])
    lines.extend(
        f"{'OK' if ok else 'FAIL':4}  {label:<{width}}  {detail}"
        for label, ok, detail in checks
        if not label.startswith("Game: ")
    )
    lines.extend(
        [
            f"Pinned Meta Horizon Link packages: {len(META_PACKAGES)} (version {META_VERSION})",
            "",
            f"[Library: {len(installed)} games]",
            *(game_lines or ["No games registered."]),
            "",
            "[Recent launches]",
        ]
    )
    lines.extend(_launch_report_lines(launches))
    lines.extend(
        [
            "",
            "[Launch vs doctor build comparison]",
            *_component_comparison(launches, current_components),
        ]
    )
    lines.extend(_debug_settings_lines(launches))
    evidence, journal_since = _collect_evidence(paths, launches, doctor_started)
    if journal_since:
        lines.extend(
            [
                "",
                "[Evidence correlation]",
                f"Newest launch window: {journal_since} to "
                f"{_capped_journal_until(journal_since)}",
                f"Evidence launch RiftLift build: "
                f"{launches[0].get('riftlift_version', 'unknown')}",
                f"Doctor RiftLift build: {__version__}",
            ]
        )
    lines.extend(["", "[Likely cause]", *_likely_cause(evidence, launches)])
    recommendations = _recommendations(
        checks, launches, debug_logging, evidence, current_components
    )
    if recommendations:
        lines.extend(["", "[Recommended next steps]"])
        lines.extend(f"- {item}" for item in recommendations)
    summary, stale_components = _summary_lines(
        checks, launches, current_components, expected_components
    )
    lines.extend(summary)
    lines.extend(_evidence_lines(evidence, launches))
    report = _bounded_report(lines)
    failed = sum(not ok for _, ok, _ in checks)
    latest_failed = bool(launches) and _failed_launch(launches[0])
    return report, failed == 0 and not stale_components and not latest_failed


def upload_report(report: str) -> str:
    request = urllib.request.Request(
        PASTE_URL,
        data=report.encode(),
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": f"RiftLift/{__version__}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status not in (201, 206):
            raise OSError(f"paste service returned HTTP {response.status}")
        url = response.read(1024).decode(errors="replace").strip()
    if not url.startswith("https://paste.rs/"):
        raise OSError("paste service returned an invalid URL")
    return url


def doctor(paths: Paths, *, paste: bool = True) -> int:
    report, healthy = build_report(paths)
    print(report, end="")
    if paste:
        try:
            url = upload_report(report)
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            print(f"\nPaste upload failed: {redact(str(error))}")
            print("The complete report is still available above.")
        else:
            print(f"\nShare this diagnostic paste: {url}")
    return 0 if healthy else 1
