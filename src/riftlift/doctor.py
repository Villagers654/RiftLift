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
from .diagnostics import recent_launches, redact, utc_now
from .launch import runtime_backend
from .runtime import META_PACKAGES, active_runtime_json, native_xr_bridge, proton_dir
from .steam import steam_root

PASTE_URL = "https://paste.rs/"
_ERROR_LINE = re.compile(
    r"(?i)\b(error|failed?|failure|fatal|panic|crash|exception|timed? out|"
    r"unsupported|not found|device lost|segfault|denied)\b"
)
_LOG_NAMES = {"player.log", "output_log.txt", "crash.log", "error.log"}
_MAX_REPORT = 48 * 1024


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
        return True, f"{redact(str(target))} ({name}; {library})"
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


def _recent_journal_errors() -> list[str]:
    output = _command(
        [
            "journalctl",
            "--user",
            "--since=-24h",
            "--no-pager",
            "-o",
            "short-iso",
            "-n",
            "300",
            "--grep=(riftlift|xrizer|rift_runtime|openxr|wineopenxr|proton)",
        ],
        timeout=5,
    )
    result = []
    for line in output.splitlines():
        noisy = (
            "Failed to parse bindings for ViveController" in line
            or 'Failed to parse bindings for Unknown("holographic_controller")' in line
            or "Unsupported application type: Utility" in line
            or "riftlift-validate-" in line
            or "riftlift-probe-" in line
        )
        if _ERROR_LINE.search(line) and not noisy:
            result.append(redact(line.strip())[:600])
    return result[-8:]


def _recent_game_log_errors(paths: Paths) -> list[str]:
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
                if name.casefold() in _LOG_NAMES:
                    candidates.append(Path(root) / name)
            if len(candidates) > 200:
                break
    except OSError:
        return []

    recent: list[tuple[float, Path]] = []
    for candidate in candidates:
        try:
            recent.append((candidate.stat().st_mtime, candidate))
        except OSError:
            pass
    result = []
    for _, candidate in sorted(recent, reverse=True)[:3]:
        try:
            lines = candidate.read_text(errors="replace").splitlines()[-500:]
        except OSError:
            continue
        matches = [
            redact(line.strip())[:600] for line in lines if _ERROR_LINE.search(line)
        ]
        if matches:
            result.append(f"{redact(str(candidate))}:")
            result.extend(f"  {line}" for line in matches[-6:])
    return result


def build_report(paths: Paths) -> tuple[str, bool]:
    checks: list[tuple[str, bool, str]] = []

    def check(label: str, action: object) -> None:
        try:
            value = action() if callable(action) else action
            checks.append((label, True, redact(str(value))))
        except Exception as error:
            checks.append((label, False, redact(str(error))))

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
    for backend in ("openxr", "openvr"):
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

    installed = games(paths)
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
        f"psvr2-fossvr.service: {_service_state('psvr2-fossvr.service')}",
        f"psvr2-fossvr-wayvr.service: {_service_state('psvr2-fossvr-wayvr.service')}",
        f"monado.service: {_service_state('monado.service')}",
        f"XR_RUNTIME_JSON: {redact(os.environ.get('XR_RUNTIME_JSON', '<unset>'))}",
        f"VR_OVERRIDE: {redact(os.environ.get('VR_OVERRIDE', '<unset>'))}",
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
    launches = recent_launches(paths)
    if not launches:
        lines.append(
            "No structured launch history yet (new launches will appear here)."
        )
    for item in launches:
        if item.get("event") != "finished":
            outcome = "INTERRUPTED (no completion recorded)"
        elif item.get("error"):
            outcome = f"ERROR: {item['error']}"
        else:
            outcome = f"exit {item.get('exit_code', '?')} after {item.get('duration_seconds', '?')}s"
        capabilities = ",".join(item.get("capabilities", [])) or "none"
        lines.append(
            f"{item.get('at', '?')}  {item.get('game', item.get('slug', '?'))}  "
            f"{item.get('backend', '?')}  {outcome}  caps={capabilities}"
        )

    evidence = [*_recent_journal_errors(), *_recent_game_log_errors(paths)]
    lines.extend(["", "[Recent error evidence]"])
    lines.extend(evidence or ["No matching recent XR/game errors found."])
    lines.extend(
        [
            "",
            f"[Summary] {passed} passed, {failed} failed",
        ]
    )
    report = redact("\n".join(lines).strip() + "\n")
    if len(report.encode()) > _MAX_REPORT:
        encoded = report.encode()[: _MAX_REPORT - 100]
        report = encoded.decode(errors="ignore") + "\n[report truncated]\n"
    return report, failed == 0


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
