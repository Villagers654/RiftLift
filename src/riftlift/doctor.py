from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
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
    META_CLIENT_COMPAT_MARKER,
    META_PACKAGES,
    OPENVR_RUNTIME_VERSION,
    PROTON_VERSION,
    RUNTIME_VERSION,
    active_runtime_json,
    envision_profile,
    debug_logging_active,
    native_xr_bridge,
    proton_dir,
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


def _vulkan_summary() -> str:
    output = _command(["vulkaninfo", "--summary"], timeout=6)
    details = []
    for line in output.splitlines():
        stripped = line.strip()
        if any(
            stripped.startswith(field)
            for field in ("deviceName", "driverName", "driverInfo", "apiVersion")
        ):
            details.append(stripped)
    return "; ".join(details[:8]) or "vulkaninfo unavailable"


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
            f"205.0 sha256:{sha256[:12]}" if sha256 else "missing/unknown"
        )
    client_patch = support / "oculus-client" / META_CLIENT_COMPAT_MARKER
    meta_builds["meta_client_patch"] = (
        META_CLIENT_COMPAT_MARKER if client_patch.is_file() else "missing"
    )
    return {
        "riftlift": __version__,
        "compat_runtime": runtime_build,
        "openvr_runtime": _installed_marker(paths.tools / "openvr-runtime"),
        "proton": proton_build,
        **meta_builds,
        "platform_bridge": f"compat-runtime:{runtime_build}",
        **system_build_components(),
        **xr_build_components(),
    }


def _expected_components() -> dict[str, str]:
    return {
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
            r"steamvr|vrserver|vrcompositor|gamescope)",
            line,
        ):
            result.append(redact(line.strip())[:600])
    return result[-12:]


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
            "wivrn|monado|steamvr|vrserver|vrcompositor|vulkan|dxvk|vkd3d)",
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
            r"(?i)(riftlift|wine|proton|steam|openxr|xrizer|wivrn|monado|\.exe)",
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


def _launch_epoch(launches: list[dict[str, object]]) -> float | None:
    values = []
    for launch in launches:
        value = launch.get("started_at", launch.get("at"))
        if not isinstance(value, str):
            continue
        try:
            values.append(datetime.fromisoformat(value).timestamp())
        except ValueError:
            pass
    return max(values) - 5 if values else None


def _launch_end_epoch(launches: list[dict[str, object]]) -> float | None:
    starts = []
    for launch in launches:
        value = launch.get("started_at", launch.get("at"))
        if not isinstance(value, str):
            continue
        try:
            starts.append((datetime.fromisoformat(value).timestamp(), launch))
        except ValueError:
            pass
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
        elif candidate.name.casefold() in {
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
        elif launch.get("event") != "finished" or launch.get("exit_code") != 0:
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
        try:
            lines = _tail_lines(target)[-1000:]
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
            result.extend(f"  {line}" for line in matches[-6:])
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
        if matches:
            result.append(f"{redact(str(target))}:")
            result.extend(f"  {line}" for line in matches[-5:])
    return result


def _recommendations(
    checks: list[tuple[str, bool, str]],
    launches: list[dict[str, object]],
    debug_logging: bool,
    evidence: list[str],
    current_components: dict[str, str],
) -> list[str]:
    failed_labels = {label for label, ok, _detail in checks if not ok}
    result: list[str] = []
    if "Active OpenXR runtime" in failed_labels:
        result.append(
            "Configure a working OpenXR runtime, then rerun `riftlift doctor`."
        )
    if "Steam" in failed_labels:
        result.append("Start and sign in to Steam once so RiftLift can find it.")
    setup_labels = {
        "GE-Proton",
        "Windows ABI launcher",
        "OpenXR ABI bridge",
        "OpenVR ABI bridge",
        "Native OPENXR unixlib",
        "Native OPENVR unixlib",
        "RiftLift OpenVR translator",
        "Meta client",
        "Platform bridge",
    }
    if failed_labels & setup_labels:
        result.append(
            "Run `riftlift setup` to repair missing compatibility components."
        )
    if "Meta sign-in" in failed_labels:
        result.append("Run `riftlift login` before installing Meta-owned games.")
    if any(label.startswith("Game: ") for label in failed_labels):
        result.append("Repair or re-register games whose executable is marked missing.")
    unsuccessful = [
        item
        for item in launches
        if item.get("event") != "finished"
        or item.get("error")
        or item.get("exit_code") != 0
    ]
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
    if (
        "xr_error_runtime_unavailable" in evidence_text
        or "openxr result -51" in evidence_text
        or "xrcreateinstance failed: -51" in evidence_text
    ):
        result.append(
            "Start the XR service in Envision, confirm Monado remains running, "
            "then retry. The selected runtime manifest exists, but its service "
            "was unavailable when the game initialized OpenXR."
        )
    if any(
        signature in evidence_text
        for signature in (
            "vk_error_device_lost",
            "gpu reset",
            "ring timeout",
            "vm fault",
        )
    ):
        result.append(
            "The Vulkan device was lost. Check `journalctl -k -b` for an amdgpu "
            "reset, then retry after restarting the XR runtime and game; disable "
            "GPU overlays or experimental upscaling if it repeats."
        )
    if "xr_error_form_factor_unavailable" in evidence_text:
        result.append(
            "Make sure the headset is connected and the selected OpenXR runtime has "
            "an active HMD session before retrying."
        )
    if "xr_error_graphics_device_invalid" in evidence_text:
        result.append(
            "Ensure the game and OpenXR runtime select the same GPU; remove forced "
            "DXVK/VKD3D device filters and retry."
        )
    if (
        "out_of_device_memory" in evidence_text
        or "out of device memory" in evidence_text
    ):
        result.append(
            "Close GPU-heavy applications and overlays, reduce VR resolution, and "
            "retry after restarting the XR runtime."
        )
    if "please authorize this new location" in evidence_text:
        result.append(
            "Authorize the new location through the EchoVRCE account prompt, then "
            "retry. The captured log confirms that the community service was "
            "reachable."
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
            "Proton + Wine XR/Steam/Vulkan + DXVK debug + VKD3D info + "
            "Vulkan/OpenXR loader + crash reports"
            if enabled
            else "disabled"
        )
    ]
    for label, name in (
        ("Proton", "proton"),
        ("Graphics", "graphics"),
        ("Crash", "crashes"),
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


def _likely_cause(evidence: list[str], launches: list[dict[str, object]]) -> list[str]:
    joined = "\n".join(evidence).casefold()
    if "please authorize this new location" in joined:
        return [
            "High confidence: Echo VR reached the community service, but that "
            "service requires this new location to be authorized. XR and network "
            "transport initialized successfully; this is an account authorization "
            "gate, not an offline or lost-headset failure."
        ]
    if "riftlift: patched 0 executable runtime imports" in joined:
        return [
            "High confidence: RiftLift loaded, but could not intercept the game's "
            "Oculus runtime imports. This compatibility runtime build does not "
            "support the executable's loader layout."
        ]
    if (
        "xr_error_runtime_unavailable" in joined
        or "openxr result -51" in joined
        or "xrcreateinstance failed: -51" in joined
    ):
        return [
            "High confidence: the selected OpenXR manifest was found, but its "
            "runtime service was unavailable when the game initialized XR. Start "
            "the XR service in Envision and confirm Monado stays running."
        ]
    if "failed to inject" in joined or "failed to create process" in joined:
        return [
            "High confidence: the RiftLift launcher could not start or inject the "
            "Oculus compatibility bridge into the game process."
        ]
    if "gpu reset" in joined or "ring timeout" in joined or "vm fault" in joined:
        return [
            "High confidence: the kernel recorded an AMD GPU hang/reset or memory "
            "fault during the launch window."
        ]
    if "vk_error_device_lost" in joined or "device lost" in joined:
        return [
            "Strong lead: DXVK/Vulkan lost the logical GPU device. Check the "
            "Kernel/GPU journal evidence below to distinguish a driver reset from "
            "a userspace graphics failure."
        ]
    if "xr_error_form_factor_unavailable" in joined:
        return [
            "High confidence: the OpenXR runtime did not have an available headset "
            "for the game session."
        ]
    if "xr_error_graphics_device_invalid" in joined:
        return [
            "High confidence: the game and OpenXR runtime selected incompatible "
            "graphics devices."
        ]
    if "out_of_device_memory" in joined or "out of device memory" in joined:
        return ["Strong lead: the Vulkan graphics device exhausted available memory."]
    if "coredump" in joined or "segfault" in joined or "fatal" in joined:
        return ["Strong lead: a relevant process crashed during the launch window."]
    if "xr_error" in joined or "openxr" in joined and "failed" in joined:
        return ["Strong lead: OpenXR runtime or session initialization failed."]
    if "not found" in joined or "failed to load" in joined:
        return ["Strong lead: a required runtime module or game file failed to load."]
    if any(
        item.get("event") != "finished" or item.get("exit_code") is None
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
    checks: list[tuple[str, bool, str]] = []
    installed = games(paths)
    launches = recent_launches(paths)
    current_components = _current_components(paths)
    expected_components = _expected_components()

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
        try:
            required_backends.add(runtime_backend(game))
        except Exception:
            pass
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
        openvr_runtime = paths.tools / "openvr-runtime/libxrizer.so"
        checks.append(
            (
                "RiftLift OpenVR translator",
                openvr_runtime.is_file(),
                _file_identity(openvr_runtime),
            )
        )
    meta_client = (
        paths.prefix
        / "pfx/drive_c/Program Files/Oculus/Support/oculus-client/Client.exe"
    )
    checks.append(("Meta client", meta_client.is_file(), _file_identity(meta_client)))
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
        f"Vulkan: {_vulkan_summary()}",
        f"Input devices: {_connected_inputs()}",
        "",
        "[XR services]",
        f"monado.service: {_service_state('monado.service')}",
        f"wivrn.service: {_service_state('wivrn.service')}",
        f"XR_RUNTIME_JSON: {redact(os.environ.get('XR_RUNTIME_JSON', '<unset>'))}",
        f"VR_OVERRIDE: {redact(os.environ.get('VR_OVERRIDE', '<unset>'))}",
        "Debug logging: "
        + ("enabled (expanded bounded capture)" if debug_logging else "disabled"),
        "",
        "[Debug capture]",
        *_debug_capture_summary(paths, debug_logging),
        "",
        "[Relevant processes]",
        *(_relevant_processes() or ["none detected"]),
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
            f"Pinned Meta Horizon Link packages: {len(META_PACKAGES)} (version 205.0)",
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
        *_recent_debug_file_errors(paths, evidence_launches, "game"),
        *_recent_debug_file_errors(
            paths, evidence_launches, "crashes", include_tail=True
        ),
        *_recent_journal_errors(journal_since),
        *_recent_kernel_errors(journal_since),
        *_recent_coredumps(journal_since),
        *_recent_steam_log_errors(paths, evidence_launches),
        *_recent_game_log_errors(paths, evidence_launches),
    ]
    lines.extend(["", "[Likely cause]", *_likely_cause(evidence, launches)])
    recommendations = _recommendations(
        checks, launches, debug_logging, evidence, current_components
    )
    if recommendations:
        lines.extend(["", "[Recommended next steps]"])
        lines.extend(f"- {item}" for item in recommendations)
    unsuccessful_launches = sum(
        item.get("event") != "finished"
        or bool(item.get("error"))
        or item.get("exit_code") != 0
        for item in launches
    )
    successful_launches = len(launches) - unsuccessful_launches
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
    latest_failed = bool(launches) and (
        launches[0].get("event") != "finished"
        or bool(launches[0].get("error"))
        or launches[0].get("exit_code") != 0
    )
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
