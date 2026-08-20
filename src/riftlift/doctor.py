from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .auth import is_signed_in
from .config import Paths, games
from .detection import (
    is_unity_player,
    is_unreal_shipping,
    uses_d3d12_runtime,
    uses_oculus_xr_plugin,
    uses_openvr_runtime,
)
from .diagnostics import (
    launch_log_path,
    prepare_proton_logs,
    recent_launches,
    redact,
    system_build_components,
    utc_now,
)
from .launch import runtime_backend
from .runtime import (
    DXVK_SHA256,
    DXVK_VERSION,
    META_CLIENT_COMPAT_MARKER,
    META_PACKAGES,
    META_RUNTIME_SIGNED_FILES,
    META_SIGNING_ROOT_THUMBPRINT,
    META_VERSION,
    OPENVR_RUNTIME_VERSION,
    PROTON_VERSION,
    RUNTIME_VERSION,
    active_runtime_json,
    debug_logging_active,
    envision_profile,
    meta_signing_root_installed,
    native_xr_bridge,
    proton_dir,
    steamvr_runtime_for_openxr,
    xr_build_components,
)
from .steam import steam_root

PASTE_URL = "https://paste.rs/"
_ERROR_LINE = re.compile(
    r"(?i)\b(error|failed?|failure|fatal|panic|crash|exception|timed? out|"
    r"timeout|unsupported|not found|device lost|segfault|denied|gpu reset|"
    r"vm fault|page fault|hung|oom|xid)\b"
)
_LOG_NAMES = {
    "player.log",
    "output_log.txt",
    "crash.log",
    "error.log",
    "riftliftlauncher.txt",
    "riftlift-runtime-trace.log",
}
_MAX_REPORT = 48 * 1024
_MAX_LOG_TAIL = 512 * 1024
_MAX_PRIORITIZED_LOG_CANDIDATES = 256
_MAX_PRIORITIZED_LOG_LINES = 12


def _cancelled_launch(launch: dict[str, object]) -> bool:
    return (
        launch.get("event") == "finished" and launch.get("error") == "KeyboardInterrupt"
    )


def _failed_launch(launch: dict[str, object]) -> bool:
    return not _cancelled_launch(launch) and (
        launch.get("event") != "finished"
        or bool(launch.get("error"))
        or launch.get("exit_code") != 0
    )


def _command(arguments: list[str], timeout: float = 4) -> str:
    try:
        result = subprocess.run(
            arguments,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()


def _os_name() -> str:
    try:
        values = {}
        for line in Path("/etc/os-release").read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value.strip().strip('"')
        return values.get("PRETTY_NAME", values.get("NAME", "unknown"))
    except OSError:
        return "unknown"


def _cpu_name() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
            if line.casefold().startswith("model name"):
                return line.partition(":")[2].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _memory() -> str:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                gib = int(line.split()[1]) / 1024 / 1024
                return f"{gib:.1f} GiB"
    except (OSError, ValueError, IndexError):
        pass
    return "unknown"


def _gpu_summary() -> str:
    output = _command(["lspci", "-nnk"], timeout=3)
    lines = output.splitlines()
    devices: list[str] = []
    for index, line in enumerate(lines):
        if "VGA compatible controller" not in line and "3D controller" not in line:
            continue
        detail = [line.strip()]
        for extra in lines[index + 1 : index + 5]:
            if extra and not extra[0].isspace():
                break
            if "Kernel driver in use:" in extra:
                detail.append(extra.strip())
        devices.append("; ".join(detail))
    return " | ".join(devices[:3]) or "not detected"


def _service_state(name: str) -> str:
    load_state = _command(
        ["systemctl", "--user", "show", name, "-p", "LoadState", "--value"],
        timeout=2,
    )
    if load_state != "loaded":
        return "not installed"
    state = _command(["systemctl", "--user", "is-active", name], timeout=2)
    return state or "unknown"


def _runtime_description() -> tuple[bool, str]:
    try:
        target = active_runtime_json()
        payload = json.loads(target.read_text())
        runtime = payload.get("runtime", {})
        name = runtime.get("name", "unnamed")
        library = Path(str(runtime.get("library_path", "unknown"))).name
        envision = envision_profile()
        source = ""
        if envision is not None and envision.manifest == target:
            source = (
                f"; Envision profile {envision.name} [{envision.uuid}], "
                f"environment={','.join(sorted(envision.environment)) or 'none'}"
            )
        return True, f"{redact(str(target))} ({name}; {library}{source})"
    except Exception as error:
        return False, redact(str(error))


def _file_identity(path: Path) -> str:
    if not path.is_file():
        return "missing"
    try:
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
        digest = hasher.hexdigest()[:12]
        return f"present, {path.stat().st_size // 1024} KiB, sha256 {digest}"
    except OSError as error:
        return f"unreadable: {error}"


def _proton_version(path: Path) -> str:
    for candidate in (path / "version", path / "files/version"):
        try:
            value = candidate.read_text(errors="replace").strip()
        except OSError:
            continue
        if value:
            return value[:160]
    return path.name


def _installed_marker(path: Path) -> str:
    try:
        value = (path / ".riftlift-version").read_text(errors="replace").strip()
    except OSError:
        return "missing"
    return value[:160] or "unknown"


def _installed_dxvk(path: Path) -> tuple[bool, str]:
    marker = path / "files/lib/wine/dxvk/.riftlift-dxvk.json"
    try:
        payload = json.loads(marker.read_text())
        version = str(payload.get("version", ""))
        expected_files = payload.get("files", {})
        if not isinstance(expected_files, dict) or not expected_files:
            raise ValueError("file manifest is empty")
        damaged = []
        for relative, expected in expected_files.items():
            target = marker.parent / str(relative)
            if (
                not target.is_file()
                or hashlib.sha256(target.read_bytes()).hexdigest() != expected
            ):
                damaged.append(str(relative))
        if damaged:
            return (
                False,
                f"{version or 'unknown'}; missing or changed: {', '.join(damaged)}",
            )
        artifact = str(payload.get("artifact_sha256", ""))[:12]
        return (
            version == DXVK_VERSION and artifact == DXVK_SHA256[:12],
            f"{version} sha256:{artifact or 'unknown'}",
        )
    except (OSError, json.JSONDecodeError, AttributeError, ValueError) as error:
        return False, f"missing or unreadable: {error}"


def _current_components(paths: Paths) -> dict[str, str]:
    try:
        proton = proton_dir()
        proton_build = (
            _proton_version(proton) if (proton / "proton").is_file() else "missing"
        )
    except Exception:
        # Build identity is diagnostic metadata, not a prerequisite for the
        # report. A clean host must say Proton is missing instead of aborting
        # before the guarded core checks can explain that Steam is absent.
        proton_build = "missing"
        proton = Path("/nonexistent")
    dxvk_ok, dxvk_detail = _installed_dxvk(proton)
    dxvk_build = dxvk_detail if dxvk_ok else f"invalid ({dxvk_detail})"
    runtime_build = _installed_marker(paths.tools / "rift-runtime")
    support = paths.prefix / "pfx/drive_c/Program Files/Oculus/Support"
    meta_builds: dict[str, str] = {}
    for package in META_PACKAGES:
        marker = support / package.name / ".riftlift-package.json"
        try:
            sha256 = str(json.loads(marker.read_text()).get("sha256", ""))
        except (OSError, json.JSONDecodeError):
            sha256 = ""
        meta_builds[f"meta_{package.name.replace('-', '_')}"] = (
            f"{META_VERSION} sha256:{sha256[:12]}" if sha256 else "missing/unknown"
        )
    client_patch = support / "oculus-client" / META_CLIENT_COMPAT_MARKER
    meta_builds["meta_client_patch"] = (
        META_CLIENT_COMPAT_MARKER if client_patch.is_file() else "missing"
    )
    bundled_xrizer = _installed_marker(paths.tools / "openvr-runtime")
    selected_openvr = bundled_xrizer
    openvr_transport = f"XRizer {bundled_xrizer} -> active OpenXR runtime"
    try:
        steamvr = steamvr_runtime_for_openxr(active_runtime_json())
    except Exception:
        steamvr = None
    if steamvr is not None:
        try:
            steamvr_build = (steamvr / "bin/version.txt").read_text().strip()
        except OSError:
            steamvr_build = "unknown"
        selected_openvr = f"SteamVR {steamvr_build[:160]}"
        openvr_transport = "SteamVR direct (no XRizer)"
    return {
        "riftlift": __version__,
        "compat_runtime": runtime_build,
        "bundled_xrizer": bundled_xrizer,
        "openvr_runtime": selected_openvr,
        "openvr_transport": openvr_transport,
        "proton": proton_build,
        "dxvk": dxvk_build,
        **meta_builds,
        "platform_bridge": f"compat-runtime:{runtime_build}",
        # Doctor must remain passive while an XR compositor is live. Starting a
        # second Vulkan client (vulkaninfo) or Envision process just to obtain a
        # version can disturb runtimes and single-instance GUI builds.
        **system_build_components(probe_vulkan=False),
        **xr_build_components(),
    }


def _expected_components() -> dict[str, str]:
    return {
        "riftlift": __version__,
        "compat_runtime": RUNTIME_VERSION,
        "bundled_xrizer": OPENVR_RUNTIME_VERSION,
        "proton": PROTON_VERSION,
        "dxvk": f"{DXVK_VERSION} sha256:{DXVK_SHA256[:12]}",
        **{
            f"meta_{package.name.replace('-', '_')}": f"{META_VERSION} sha256:{package.sha256[:12]}"
            for package in META_PACKAGES
        },
        "meta_client_patch": META_CLIENT_COMPAT_MARKER,
        "platform_bridge": f"compat-runtime:{RUNTIME_VERSION}",
    }


def _component_matches(name: str, installed: str, expected: str) -> bool:
    if name == "proton":
        return installed == expected or installed.endswith(f" {expected}")
    return installed == expected


def _component_comparison(
    launches: list[dict[str, object]], current: dict[str, str]
) -> list[str]:
    if not launches:
        return ["No launch snapshot is available for comparison."]
    newest = launches[0]
    captured = newest.get("components")
    if not isinstance(captured, dict):
        legacy = newest.get("riftlift_version", "unknown")
        return [
            "LEGACY/UNKNOWN: this launch predates the complete component snapshot "
            f"(captured RiftLift={legacy}; doctor RiftLift={__version__}). Reproduce "
            "with the current build for a reliable comparison."
        ]
    lines: list[str] = []
    names = list(_expected_components())
    names.extend(
        sorted(name for name in set(captured) | set(current) if name not in names)
    )
    for name in names:
        before = str(captured.get(name, "unknown"))
        now = current.get(name, "unknown")
        if before.startswith("not-used("):
            state = "NOT USED"
        else:
            state = "SAME" if before == now else "CHANGED"
        lines.append(f"{state:7} {name}: launch={before}; doctor={now}")
    return lines


def _connected_inputs() -> str:
    names = []
    try:
        lines = Path("/proc/bus/input/devices").read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if not line.startswith("N: Name="):
            continue
        name = line.partition("=")[2].strip('"')
        if re.search(
            r"(?i)(controller|gamepad|sense|oculus|vive|index|quest|vr2)", name
        ):
            names.append(name)
    return ", ".join(dict.fromkeys(names)) or "none detected"


def _relevant_processes() -> list[str]:
    output = _command(["ps", "-eo", "pid=,etimes=,comm="], timeout=3)
    result = []
    for line in output.splitlines():
        fields = line.split(maxsplit=1)
        if fields and fields[0] == str(os.getpid()):
            continue
        if re.search(
            r"(?i)(riftlift|wine|wineserver|proton|openxr|xrizer|wivrn|monado|"
            r"steamvr|vrserver|vrcompositor|gamescope|envision)",
            line,
        ):
            result.append(redact(line.strip())[:600])
    return result[-12:]


def _process_names(processes: list[str]) -> set[str]:
    names: set[str] = set()
    for line in processes:
        fields = line.split(maxsplit=2)
        if len(fields) == 3:
            names.add(fields[2].casefold())
    return names


def _recent_journal_errors(since: str | None = None) -> list[str]:
    # Steam probes OpenXR on its own and emits the same loader messages as a
    # game.  Without a recorded RiftLift launch there is no sound way to
    # attribute those messages to us, so do not include a global 24-hour
    # scrape in the launch evidence section.
    if since is None:
        return []
    output = _command(
        [
            "journalctl",
            "--user",
            f"--since={since}",
            f"--until={_capped_journal_until(since)}",
            "--no-pager",
            "-o",
            "short-iso",
            "-n",
            "300",
            "--grep=(riftlift|xrizer|rift_runtime|openxr|wineopenxr|proton|"
            "wivrn|monado|envision|steamvr|vrserver|vrcompositor|vulkan|dxvk|vkd3d)",
        ],
        timeout=5,
    )
    result = []
    for line in output.splitlines():
        if _ERROR_LINE.search(line) and not _noisy_evidence(line):
            result.append(redact(line.strip())[:600])
    return (
        (["User/XR journal:"] + [f"  {line}" for line in result[-10:]])
        if result
        else []
    )


def _recent_kernel_errors(since: str | None = None) -> list[str]:
    if since is None:
        return []
    output = _command(
        [
            "journalctl",
            "-k",
            f"--since={since}",
            f"--until={_capped_journal_until(since)}",
            "--no-pager",
            "-o",
            "short-iso",
            "-n",
            "400",
            "--grep=(amdgpu|drm|gpu|vulkan|xid|oom)",
        ],
        timeout=5,
    )
    matches = [
        redact(line.strip())[:600]
        for line in output.splitlines()
        if _ERROR_LINE.search(line) and not _noisy_evidence(line)
    ]
    return (
        (["Kernel/GPU journal:"] + [f"  {line}" for line in matches[-10:]])
        if matches
        else []
    )


def _recent_coredumps(since: str | None = None) -> list[str]:
    if since is None or not shutil.which("coredumpctl"):
        return []
    output = _command(
        [
            "coredumpctl",
            f"--since={since}",
            f"--until={_capped_journal_until(since)}",
            "--no-pager",
            "--no-legend",
            "list",
        ],
        timeout=5,
    )
    matches = [
        redact(line.strip())[:600]
        for line in output.splitlines()
        if "steamwebhelper" not in line.casefold()
        and re.search(
            r"(?i)(riftlift|wine|proton|steam|openxr|xrizer|wivrn|monado|envision|\.exe)",
            line,
        )
    ]
    return (
        (["Recent relevant coredumps:"] + [f"  {line}" for line in matches[-6:]])
        if matches
        else []
    )


def _noisy_evidence(line: str) -> bool:
    return (
        "Failed to parse bindings for ViveController" in line
        or 'Failed to parse bindings for Unknown("holographic_controller")' in line
        or "Unsupported application type: Utility" in line
        or "riftlift-validate-" in line
        or "riftlift-probe-" in line
        or ("Registered" in line and "drm panic" in line)
        or "Listening on systemd-oomd" in line
    )


def _capped_journal_until(since: str) -> str:
    try:
        start = datetime.fromisoformat(since)
    except ValueError:
        return "now"
    now = datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return min(now, start + timedelta(hours=6)).isoformat()


def _tail_lines(path: Path) -> list[str]:
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        offset = max(0, size - _MAX_LOG_TAIL)
        stream.seek(offset)
        payload = stream.read()
    if offset:
        _partial, separator, payload = payload.partition(b"\n")
        if not separator:
            return []
    return payload.decode(errors="replace").splitlines()


def _proton_line_priority(line: str) -> int:
    """Rank useful Proton evidence without relying on title-specific errors."""
    folded = line.casefold()
    if _noisy_evidence(line):
        return 0
    # These are loader/debug-print implementation details, not the exception or
    # message that prompted them. Keeping them tends to hide the application
    # error during Wine's very noisy process teardown.
    if (
        "trace:seh:dispatch_exception code=40010006" in folded
        or 'warn:seh:dispatch_exception "' in folded
        or "warn:module:find_builtin_dll cannot find builtin library" in folded
    ):
        return 0

    application_output = "debugstr:outputdebugstring" in folded
    vr_related = any(
        marker in folded
        for marker in ("riftlift", "openxr", "xr_", "ovr", "oculus", "vrclient")
    )
    crash = any(
        marker in folded
        for marker in (
            "exception_access_violation",
            "unhandled exception",
            "access violation",
            "segfault",
            "page fault",
            "fatal",
            "panic",
            "crash detected",
        )
    ) or bool(re.search(r"\b(?:_?w?assert|assertion)\b", folded))
    failure = bool(_ERROR_LINE.search(line))

    if application_output and vr_related and failure:
        return 120
    if application_output and crash:
        return 115
    if crash:
        return 110
    if "riftlift:" in folded and failure:
        return 105
    if vr_related and failure:
        return 100
    if "riftlift: patched" in folded:
        return 90
    if application_output and failure:
        # Bare "[ERROR]" fragments are less useful than the adjacent message.
        return 55 if re.search(r'outputdebugstring[aw]? "\[error\]\s*"', folded) else 85
    if ":err:" in folded and failure:
        return 75
    if failure and ":fixme:" not in folded:
        return 45
    return 0


def _prioritized_proton_lines(path: Path) -> list[str]:
    """Scan a retained log completely while keeping memory and output bounded."""
    candidates: list[tuple[int, int, str]] = []
    try:
        with path.open(errors="replace") as stream:
            for index, line in enumerate(stream):
                line = line.strip()
                priority = _proton_line_priority(line)
                if not priority:
                    continue
                candidates.append((priority, index, redact(line)[:600]))
                if len(candidates) > _MAX_PRIORITIZED_LOG_CANDIDATES * 2:
                    candidates = sorted(
                        candidates, key=lambda item: (-item[0], item[1])
                    )[:_MAX_PRIORITIZED_LOG_CANDIDATES]
    except OSError:
        return []

    selected: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    ranked = sorted(candidates, key=lambda value: (-value[0], value[1]))
    # Once the log contains high-value application/XR evidence, do not pad the
    # report with much weaker generic errors merely to reach the line limit.
    minimum_priority = max(45, ranked[0][0] - 30) if ranked else 0
    for item in ranked:
        if item[0] < minimum_priority:
            continue
        # Wine can emit the same application message twice through neighboring
        # debug channels. Prefer distinct evidence over repeated boilerplate.
        normalized = re.sub(r"^\d+\.\d+:[0-9a-f]+:[0-9a-f]+:", "", item[2])
        if normalized in seen:
            continue
        seen.add(normalized)
        selected.append(item)
        if len(selected) == _MAX_PRIORITIZED_LOG_LINES:
            break
    return [item[2] for item in sorted(selected, key=lambda value: value[1])]


def _launch_epoch(launches: list[dict[str, object]]) -> float | None:
    values = []
    for launch in launches:
        value = launch.get("started_at", launch.get("at"))
        if not isinstance(value, str):
            continue
        with contextlib.suppress(ValueError):
            values.append(datetime.fromisoformat(value).timestamp())
    return max(values) - 5 if values else None


def _launch_end_epoch(launches: list[dict[str, object]]) -> float | None:
    starts = []
    for launch in launches:
        value = launch.get("started_at", launch.get("at"))
        if not isinstance(value, str):
            continue
        with contextlib.suppress(ValueError):
            starts.append((datetime.fromisoformat(value).timestamp(), launch))
    if not starts:
        return None
    started, latest = max(starts, key=lambda item: item[0])
    finished = latest.get("finished_at")
    if isinstance(finished, str):
        try:
            return datetime.fromisoformat(finished).timestamp() + 5
        except ValueError:
            pass
    return min(datetime.now(timezone.utc).timestamp(), started + 6 * 60 * 60) + 5


def _recent_game_log_errors(
    paths: Paths, launches: list[dict[str, object]]
) -> list[str]:
    users = paths.prefix / "pfx/drive_c/users"
    if not users.is_dir():
        return []
    candidates: list[Path] = []
    try:
        for root, directories, files in os.walk(users):
            directories[:] = [
                item
                for item in directories
                if item.casefold() not in {"cache", "gpucache", "shadercache"}
            ]
            for name in files:
                if name.casefold() in _LOG_NAMES or (
                    Path(root).name.casefold() == "logs"
                    and name.casefold().endswith((".log", ".txt"))
                ):
                    candidates.append(Path(root) / name)
            if len(candidates) > 200:
                break
    except OSError:
        return []

    earliest = _launch_epoch(launches)
    latest = _launch_end_epoch(launches)
    recent: list[tuple[float, Path]] = []
    for candidate in candidates:
        try:
            modified = candidate.stat().st_mtime
            if (
                earliest is not None
                and latest is not None
                and earliest <= modified <= latest
            ):
                recent.append((modified, candidate))
        except OSError:
            pass
    result = []
    include_diagnostic_tail = any(_failed_launch(launch) for launch in launches)
    for _, candidate in sorted(recent, reverse=True)[:3]:
        try:
            lines = _tail_lines(candidate)[-500:]
        except OSError:
            continue
        matches = [
            redact(line.strip())[:600]
            for line in lines
            if _ERROR_LINE.search(line) and not _noisy_evidence(line)
        ]
        if matches:
            result.append(f"{redact(str(candidate))}:")
            result.extend(f"  {line}" for line in matches[-6:])
        elif include_diagnostic_tail and candidate.name.casefold() in {
            "riftliftlauncher.txt",
            "riftlift-runtime-trace.log",
        }:
            tail = [redact(line.strip())[:600] for line in lines[-12:] if line.strip()]
            if tail:
                result.append(f"{redact(str(candidate))}:")
                result.extend(f"  {line}" for line in tail)
    return result


def _recent_launch_log_errors(
    paths: Paths, launches: list[dict[str, object]]
) -> list[str]:
    result: list[str] = []
    for launch in launches:
        launch_id = launch.get("id")
        if not isinstance(launch_id, str):
            continue
        target = launch_log_path(paths, launch_id)
        try:
            lines = _tail_lines(target)[-800:]
        except OSError:
            continue
        matches = [
            redact(line.strip())[:600]
            for line in lines
            if (_ERROR_LINE.search(line) or "RiftLift: patched" in line)
            and not _noisy_evidence(line)
        ]
        if matches:
            result.append(f"{redact(str(target))}:")
            result.extend(f"  {line}" for line in matches[-8:])
        elif _failed_launch(launch):
            tail = [
                redact(line.strip())[:600]
                for line in lines[-8:]
                if line.strip() and not _noisy_evidence(line)
            ]
            if tail:
                result.append(f"{redact(str(target))} (tail):")
                result.extend(f"  {line}" for line in tail)
    return result


def _recent_proton_log_errors(
    paths: Paths, launches: list[dict[str, object]]
) -> list[str]:
    earliest = _launch_epoch(launches)
    latest = _launch_end_epoch(launches)
    if earliest is None or latest is None:
        return []
    # Proton logs are shared across launches and survive restarts. Only surface
    # files touched during the launch window shown by this report, with a small
    # tolerance for coarse filesystem timestamps.
    directory = prepare_proton_logs(paths)
    try:
        candidates = sorted(
            (
                item
                for item in directory.glob("*.log")
                if item.is_file() and earliest <= item.stat().st_mtime <= latest
            ),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[:2]
    except OSError:
        return []
    result: list[str] = []
    for target in candidates:
        matches = _prioritized_proton_lines(target)
        if matches:
            result.append(f"{redact(str(target))}:")
            result.extend(f"  {line}" for line in matches)
    return result


def _recent_debug_file_errors(
    paths: Paths,
    launches: list[dict[str, object]],
    directory_name: str,
    *,
    include_tail: bool = False,
) -> list[str]:
    earliest = _launch_epoch(launches)
    latest = _launch_end_epoch(launches)
    if earliest is None or latest is None:
        return []
    directory = paths.data / "diagnostics" / directory_name
    try:
        candidates = sorted(
            (
                item
                for item in directory.iterdir()
                if item.is_file() and earliest <= item.stat().st_mtime <= latest
            ),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[:4]
    except OSError:
        return []
    result = []
    for target in candidates:
        try:
            lines = _tail_lines(target)[-1000:]
        except OSError:
            continue
        matches = [
            redact(line.strip())[:600]
            for line in lines
            if _ERROR_LINE.search(line) and not _noisy_evidence(line)
        ]
        selected = (
            matches[-8:]
            if matches
            else (
                [redact(line.strip())[:600] for line in lines[-8:] if line.strip()]
                if include_tail
                else []
            )
        )
        if selected:
            result.append(f"{redact(str(target))}:")
            result.extend(f"  {line}" for line in selected)
    return result


def _recent_steam_log_errors(
    paths: Paths, launches: list[dict[str, object]]
) -> list[str]:
    earliest = _launch_epoch(launches)
    latest = _launch_end_epoch(launches)
    if earliest is None or latest is None:
        return []
    try:
        directory = steam_root() / "logs"
    except Exception:
        return []
    try:
        candidates = sorted(
            (
                item
                for item in directory.iterdir()
                if item.is_file()
                and item.suffix.casefold() in {".txt", ".log"}
                and earliest <= item.stat().st_mtime <= latest
            ),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[:8]
    except OSError:
        return []
    steamvr_launch = any(
        isinstance(launch.get("components"), dict)
        and (
            str(launch["components"].get("openvr_transport", "")).startswith(
                "SteamVR direct"
            )
            or str(launch["components"].get("openxr_runtime", "")).startswith(
                "SteamVR:"
            )
        )
        for launch in launches
    )
    result = []
    for target in candidates:
        if not re.search(
            r"(?i)(vr|openxr|vulkan|shader|console|stderr|connection|webhelper)",
            target.name,
        ):
            continue
        try:
            lines = _tail_lines(target)[-600:]
        except OSError:
            continue
        matches = [
            redact(line.strip())[:600]
            for line in lines
            if _ERROR_LINE.search(line) and not _noisy_evidence(line)
        ]
        lifecycle = (
            [
                redact(line.strip())[:600]
                for line in lines
                if re.search(
                    r"(?i)(startup with PID|Active HMD|Using existing HMD|"
                    r"New Connect message|ProcessConnected|VR_Init successful|"
                    r"application.*(?:connected|started)|submitted frame|presented)",
                    line,
                )
                and not _noisy_evidence(line)
            ][-5:]
            if steamvr_launch
            and target.name.casefold().startswith(
                ("vrserver", "vrcompositor", "vrclient")
            )
            else []
        )
        selected = list(dict.fromkeys([*matches[-5:], *lifecycle]))
        if selected:
            result.append(f"{redact(str(target))}:")
            result.extend(f"  {line}" for line in selected[-8:])
    return result


def _envision_log_directories() -> list[Path]:
    """Return known Envision log locations without invoking Envision."""
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    candidates = [cache_home / "envision/logs"]
    with contextlib.suppress(OSError):
        candidates.extend((Path.home() / ".var/app").glob("*/cache/envision/logs"))
    return list(dict.fromkeys(candidates))


def _recent_envision_log_errors(
    launches: list[dict[str, object]], doctor_started: float
) -> list[str]:
    """Surface Envision evidence from the launch and current doctor windows."""
    earliest = _launch_epoch(launches)
    latest = _launch_end_epoch(launches)
    candidates: list[tuple[float, Path, bool]] = []
    for directory in _envision_log_directories():
        try:
            files = [item for item in directory.iterdir() if item.is_file()]
        except OSError:
            continue
        for item in files:
            try:
                modified = item.stat().st_mtime
            except OSError:
                continue
            during_launch = (
                earliest is not None
                and latest is not None
                and earliest <= modified <= latest
            )
            during_doctor = modified >= doctor_started - 5
            if during_launch or during_doctor:
                candidates.append((modified, item, during_doctor))

    result: list[str] = []
    for _modified, target, during_doctor in sorted(candidates, reverse=True)[:2]:
        try:
            lines = _tail_lines(target)[-1000:]
        except OSError:
            continue
        correlated: list[str] = []
        for line in lines:
            timestamp = None
            try:
                value = json.loads(line).get("timestamp")
                if isinstance(value, str):
                    timestamp = datetime.fromisoformat(value).timestamp()
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                pass
            if (
                timestamp is None
                or (
                    earliest is not None
                    and latest is not None
                    and earliest <= timestamp <= latest
                )
                or doctor_started - 5 <= timestamp <= time.time() + 5
            ):
                correlated.append(line)
        matches = [
            redact(line.strip())[:600]
            for line in correlated
            if _ERROR_LINE.search(line) and not _noisy_evidence(line)
        ]
        selected = matches[-10:]
        if during_doctor and not selected:
            selected = [
                redact(line.strip())[:600] for line in correlated[-12:] if line.strip()
            ]
        if selected:
            window = "doctor run" if during_doctor else "launch window"
            result.append(f"Envision log ({window}) {redact(str(target))}:")
            result.extend(f"  {line}" for line in selected)
    return result


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
        ("doctor safety observation",),
        "High confidence: one or more XR processes disappeared while doctor was "
        "inspecting the system.",
    ),
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
    for signatures, message in _CAUSE_RULES:
        if any(signature in joined for signature in signatures):
            return [message]
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
        return [
            "High confidence: "
            + detail
            + "the game reported that VR runtime initialization failed. Treat "
            "that initialization error as primary; a later access violation or "
            "crash reporter is likely secondary. The selected evidence below "
            "preserves the game's original error text."
        ]
    if "coredump" in joined or "segfault" in joined or "fatal" in joined:
        return ["Strong lead: a relevant process crashed during the launch window."]
    if "xr_error" in joined or ("openxr" in joined and "failed" in joined):
        return ["Strong lead: OpenXR runtime or session initialization failed."]
    if "not found" in joined or "failed to load" in joined:
        return ["Strong lead: a required runtime module or game file failed to load."]
    if any(
        not _cancelled_launch(item)
        and (item.get("event") != "finished" or item.get("exit_code") is None)
        for item in launches
    ):
        return [
            "Launch state is incomplete: RiftLift has no completion record yet. The "
            "game may still be running, or RiftLift was terminated during the launch."
        ]
    if evidence:
        return [
            "No single signature is decisive; the most relevant correlated errors "
            "are listed below."
        ]
    return ["No correlated failure signature was found in the retained sources."]


def build_report(paths: Paths) -> tuple[str, bool]:
    # Take this before any component or system inspection. If a live runtime
    # disappears while doctor runs, the report must retain proof that it was
    # present when the user pressed System.
    doctor_started = time.time()
    processes_at_start = _relevant_processes()
    checks: list[tuple[str, bool, str]] = []
    installed = games(paths)
    launches = recent_launches(paths)
    current_components = _current_components(paths)
    expected_components = _expected_components()
    captured_components = launches[0].get("components") if launches else None
    cached_vulkan = None
    if isinstance(captured_components, dict):
        value = captured_components.get("system_vulkan")
        if isinstance(value, str) and value not in {"", "unavailable"}:
            cached_vulkan = value
            current_components["system_vulkan"] = value
        value = captured_components.get("envision")
        if (
            current_components.get("envision") == "not installed/unknown"
            and isinstance(value, str)
            and value
        ):
            current_components["envision"] = value

    def check(label: str, action: object) -> None:
        try:
            value = action() if callable(action) else action
            checks.append((label, True, redact(str(value))))
        except Exception as error:
            checks.append((label, False, redact(str(error))))

    debug_logging = debug_logging_active(paths)
    runtime_ok, runtime_detail = _runtime_description()
    checks.append(("Active OpenXR runtime", runtime_ok, runtime_detail))
    check("Steam", steam_root)
    check(
        "GE-Proton",
        lambda: (
            f"{redact(str(proton_dir()))} ({_proton_version(proton_dir())})"
            if (proton_dir() / "proton").is_file()
            else (_ for _ in ()).throw(FileNotFoundError("not installed"))
        ),
    )
    try:
        dxvk_ok, dxvk_detail = _installed_dxvk(proton_dir())
    except Exception as error:
        dxvk_ok, dxvk_detail = False, str(error)
    checks.append(("RiftLift DXVK compatibility", dxvk_ok, dxvk_detail))
    rift_runtime = paths.tools / "rift-runtime"
    for label, relative in (
        ("Windows ABI launcher", "RiftLiftLauncher.exe"),
        ("OpenXR ABI bridge", "RiftLiftOpenXR64.dll"),
        ("OpenVR ABI bridge", "RiftLiftOpenVR64.dll"),
    ):
        identity = _file_identity(rift_runtime / relative)
        checks.append((label, not identity.startswith("missing"), identity))
    required_backends: set[str] = set()
    for game in installed:
        with contextlib.suppress(Exception):
            required_backends.add(runtime_backend(game))
    for backend in ("openxr", "openvr"):
        if backend == "openvr" and backend not in required_backends:
            continue
        try:
            bridge = native_xr_bridge(proton_dir(), backend)
            checks.append(
                (
                    f"Native {backend.upper()} unixlib",
                    True,
                    f"{_file_identity(bridge.pe)} + {_file_identity(bridge.unix)}",
                )
            )
        except Exception as error:
            checks.append((f"Native {backend.upper()} unixlib", False, str(error)))
    if "openvr" in required_backends:
        try:
            steamvr_runtime = steamvr_runtime_for_openxr(active_runtime_json())
        except Exception:
            steamvr_runtime = None
        if steamvr_runtime is not None:
            openvr_runtime = steamvr_runtime / "bin/linux64/vrclient.so"
            runtime_label = "SteamVR OpenVR client (direct; no XRizer)"
            expected_runtime_path = steamvr_runtime
        else:
            openvr_runtime = paths.tools / "openvr-runtime/libxrizer.so"
            runtime_label = "RiftLift OpenVR translator (XRizer)"
            expected_runtime_path = openvr_runtime.parent
        checks.append(
            (runtime_label, openvr_runtime.is_file(), _file_identity(openvr_runtime))
        )
        path_registry = paths.config / "openvr/openvrpaths.vrpath"
        try:
            registry = json.loads(path_registry.read_text())
            runtime_paths = registry.get("runtime", [])
            registry_ok = (
                registry.get("version") == 1
                and isinstance(runtime_paths, list)
                and str(expected_runtime_path) in runtime_paths
            )
            registry_detail = (
                f"{redact(str(path_registry))}; runtime={redact(str(runtime_paths))}"
            )
        except (OSError, json.JSONDecodeError, AttributeError) as error:
            registry_ok = False
            registry_detail = f"{redact(str(path_registry))}: {error}"
        checks.append(("Selected OpenVR path registry", registry_ok, registry_detail))
    meta_client = (
        paths.prefix
        / "pfx/drive_c/Program Files/Oculus/Support/oculus-client/Client.exe"
    )
    checks.append(("Meta client", meta_client.is_file(), _file_identity(meta_client)))
    meta_support = paths.prefix / "pfx/drive_c/Program Files/Oculus/Support"
    meta_runtime = meta_support / "oculus-runtime"
    for name, expected in META_RUNTIME_SIGNED_FILES.items():
        target = meta_runtime / name
        identity = _file_identity(target)
        current = ""
        if target.is_file():
            with contextlib.suppress(OSError):
                current = hashlib.sha256(target.read_bytes()).hexdigest()
        checks.append(
            (
                f"Meta signed loader: {name}",
                current == expected,
                f"{identity}; expected sha256 {expected[:12]}",
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

    game_lines = []
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
        except Exception as error:
            backend = f"error: {error}"
        state = "OK" if present else "MISSING"
        game_lines.append(
            f"{state:7} {game.name} [{game.source}; {backend}; {', '.join(capabilities) or 'no engine markers'}]"
        )
        checks.append(
            (f"Game: {game.name}", present, redact(str(game.executable_path)))
        )

    width = max(len(label) for label, _, _ in checks)
    passed = sum(ok for _, ok, _ in checks)
    failed = len(checks) - passed
    lines = [
        f"RiftLift doctor {__version__}",
        f"Generated: {utc_now()}",
        "Public report: credentials, email addresses, and home paths are redacted.",
        "",
        "[Build identity at doctor run]",
        f"Doctor build: RiftLift {__version__}",
        f"Doctor module: {redact(str(Path(__file__).resolve()))}",
        *[
            f"{'OK' if _component_matches(name, current_components[name], expected) else 'MISMATCH':8} "
            f"{name}: installed={current_components[name]}; expected={expected}"
            for name, expected in expected_components.items()
        ],
        "",
        "[System]",
        f"OS: {_os_name()}",
        f"Kernel: {platform.release()} ({platform.machine()})",
        f"Desktop: {os.environ.get('XDG_CURRENT_DESKTOP', 'unknown')} / {os.environ.get('XDG_SESSION_TYPE', 'unknown')}",
        f"CPU: {_cpu_name()}",
        f"Memory: {_memory()}",
        f"GPU: {_gpu_summary()}",
        "Vulkan: "
        + (
            f"{cached_vulkan} (latest launch snapshot; active probe skipped)"
            if cached_vulkan
            else "active probe skipped; no launch snapshot available"
        ),
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
        *(processes_at_start or ["none detected"]),
        "",
        "[Graphics/XR environment]",
        *[
            f"{name}={redact(os.environ.get(name, '<unset>'))}"
            for name in (
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
        ],
        "",
        "[Core checks]",
    ]
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
    if not launches:
        lines.append(
            "No structured launch history yet (new launches will appear here)."
        )
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
            outcome = f"exit {item.get('exit_code', '?')} after {item.get('duration_seconds', '?')}s"
        capabilities = ",".join(item.get("capabilities", [])) or "none"
        lines.append(
            f"{item.get('started_at', item.get('at', '?'))}  "
            f"{item.get('game', item.get('slug', '?'))}  "
            f"{item.get('backend', '?')}  {outcome}  caps={capabilities}  "
            f"debug={'on' if item.get('debug_logging') else 'off'}  "
            f"build={item.get('riftlift_version', 'unknown')}"
        )
        components = item.get("components")
        expected_at_launch = item.get("expected_components")
        if isinstance(components, dict):
            lines.append(
                "  captured components: "
                + "; ".join(f"{key}={value}" for key, value in components.items())
            )
        if isinstance(expected_at_launch, dict) and expected_at_launch:
            lines.append(
                "  expected by launch build: "
                + "; ".join(
                    f"{key}={value}" for key, value in expected_at_launch.items()
                )
            )
    lines.extend(
        [
            "",
            "[Launch vs doctor build comparison]",
            *_component_comparison(launches, current_components),
        ]
    )
    debug_launch = next(
        (
            item
            for item in launches
            if item.get("debug_logging") and item.get("debug_settings")
        ),
        None,
    )
    if debug_launch:
        lines.extend(["", "[Most recent debug launch settings]"])
        settings = debug_launch.get("debug_settings", {})
        if isinstance(settings, dict):
            lines.extend(
                f"{key}={value}"
                for key, value in settings.items()
                if isinstance(key, str) and isinstance(value, str)
            )

    launch_times = [
        item.get("started_at", item.get("at"))
        for item in launches
        if isinstance(item.get("started_at", item.get("at")), str)
        and item.get("started_at", item.get("at"))
    ]
    journal_since = max(launch_times) if launch_times else None
    evidence_launches = launches[:1]
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
    evidence = [
        *_recent_launch_log_errors(paths, evidence_launches),
        *_recent_proton_log_errors(paths, evidence_launches),
        *_recent_debug_file_errors(paths, evidence_launches, "graphics"),
        *_recent_debug_file_errors(
            paths, evidence_launches, "openvr", include_tail=True
        ),
        *_recent_debug_file_errors(paths, evidence_launches, "game"),
        *_recent_debug_file_errors(
            paths, evidence_launches, "crashes", include_tail=True
        ),
        *_recent_journal_errors(journal_since),
        *_recent_kernel_errors(journal_since),
        *_recent_coredumps(journal_since),
        *_recent_steam_log_errors(paths, evidence_launches),
        *_recent_game_log_errors(paths, evidence_launches),
        *_recent_envision_log_errors(evidence_launches, doctor_started),
    ]
    processes_at_end = _relevant_processes()
    disappeared = _process_names(processes_at_start) - _process_names(processes_at_end)
    if disappeared:
        evidence.extend(
            [
                "Doctor safety observation:",
                "  Processes present when System was pressed but absent after "
                "inspection: " + ", ".join(sorted(disappeared)),
            ]
        )
    lines.extend(
        [
            "",
            "[Relevant processes after inspection]",
            *(processes_at_end or ["none detected"]),
        ]
    )
    lines.extend(["", "[Likely cause]", *_likely_cause(evidence, launches)])
    recommendations = _recommendations(
        checks, launches, debug_logging, evidence, current_components
    )
    if recommendations:
        lines.extend(["", "[Recommended next steps]"])
        lines.extend(f"- {item}" for item in recommendations)
    cancelled_launches = sum(_cancelled_launch(item) for item in launches)
    unsuccessful_launches = sum(_failed_launch(item) for item in launches)
    successful_launches = len(launches) - unsuccessful_launches - cancelled_launches
    stale_components = [
        name
        for name, expected in expected_components.items()
        if not _component_matches(name, current_components[name], expected)
    ]
    lines.extend(
        [
            "",
            f"[Summary] checks: {passed} passed, {failed} failed; "
            f"component builds: {len(stale_components)} mismatched; "
            f"shown launches: {successful_launches} successful, "
            f"{cancelled_launches} cancelled, "
            f"{unsuccessful_launches} failed/incomplete",
        ]
    )
    lines.extend(["", "[Recent error evidence]"])
    if evidence:
        lines.extend(evidence)
    elif launches:
        lines.append("No matching errors found during the recorded launch window.")
    else:
        lines.append(
            "Journal scan skipped: no RiftLift launch window exists to distinguish "
            "game failures from unrelated Steam OpenXR probes."
        )
    report = redact("\n".join(lines).strip() + "\n")
    if len(report.encode()) > _MAX_REPORT:
        encoded = report.encode()[: _MAX_REPORT - 100]
        report = encoded.decode(errors="ignore") + "\n[report truncated]\n"
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
