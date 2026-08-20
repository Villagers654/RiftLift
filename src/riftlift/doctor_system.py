"""Host and active-runtime facts used by RiftLift diagnostics."""

from __future__ import annotations

import json
import os
import platform
import re
from pathlib import Path

from .diagnostics import redact
from .doctor_evidence import _command
from .util import RiftLiftError
from .xr_runtime import active_runtime_json, envision_profile


def os_name() -> str:
    try:
        values = {}
        for line in Path("/etc/os-release").read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value.strip().strip('"')
        return values.get("PRETTY_NAME", values.get("NAME", "unknown"))
    except OSError:
        return "unknown"


def cpu_name() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
            if line.casefold().startswith("model name"):
                return line.partition(":")[2].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def memory() -> str:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                gib = int(line.split()[1]) / 1024 / 1024
                return f"{gib:.1f} GiB"
    except (OSError, ValueError, IndexError):
        pass
    return "unknown"


def gpu_summary() -> str:
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


def service_state(name: str) -> str:
    load_state = _command(
        ["systemctl", "--user", "show", name, "-p", "LoadState", "--value"],
        timeout=2,
    )
    if load_state != "loaded":
        return "not installed"
    state = _command(["systemctl", "--user", "is-active", name], timeout=2)
    return state or "unknown"


def runtime_description() -> tuple[bool, str]:
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
    except (
        RiftLiftError,
        OSError,
        json.JSONDecodeError,
        KeyError,
        AttributeError,
        TypeError,
    ) as error:
        return False, redact(str(error))


def connected_inputs() -> str:
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


def relevant_processes() -> list[str]:
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
