from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Paths, xdg_cache_home
from .diagnostics import launch_log_path, prepare_proton_logs, redact
from .steam import steam_root

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


def _recent_journal_errors(since: str | None = None) -> list[str]:
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
    folded = line.casefold()
    if _noisy_evidence(line):
        return 0
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
    bare_error = bool(re.search(r'outputdebugstring[aw]? "\[error\]\s*"', folded))
    priorities = (
        (application_output and vr_related and failure, 120),
        (application_output and crash, 115),
        (crash, 110),
        ("riftlift:" in folded and failure, 105),
        (vr_related and failure, 100),
        ("riftlift: patched" in folded, 90),
        (application_output and failure and not bare_error, 85),
        (":err:" in folded and failure, 75),
        (application_output and failure and bare_error, 55),
        (failure and ":fixme:" not in folded, 45),
    )
    return max((priority for matches, priority in priorities if matches), default=0)


def _prioritized_proton_lines(path: Path) -> list[str]:
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
    minimum_priority = max(45, ranked[0][0] - 30) if ranked else 0
    for item in ranked:
        if item[0] < minimum_priority:
            continue
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


def _game_log_candidates(users: Path) -> list[Path]:
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
    return candidates


def _files_in_window(
    candidates: list[Path], earliest: float | None, latest: float | None
) -> list[tuple[float, Path]]:
    if earliest is None or latest is None:
        return []
    recent: list[tuple[float, Path]] = []
    for candidate in candidates:
        try:
            modified = candidate.stat().st_mtime
        except OSError:
            continue
        if earliest <= modified <= latest:
            recent.append((modified, candidate))
    return recent


def _game_log_evidence(candidate: Path, *, include_tail: bool) -> list[str]:
    try:
        lines = _tail_lines(candidate)[-500:]
    except OSError:
        return []
    matches = [
        redact(line.strip())[:600]
        for line in lines
        if _ERROR_LINE.search(line) and not _noisy_evidence(line)
    ]
    if matches:
        return [f"{redact(str(candidate))}:", *(f"  {line}" for line in matches[-6:])]
    trace_names = {"riftliftlauncher.txt", "riftlift-runtime-trace.log"}
    if not include_tail or candidate.name.casefold() not in trace_names:
        return []
    tail = [redact(line.strip())[:600] for line in lines[-12:] if line.strip()]
    if not tail:
        return []
    return [f"{redact(str(candidate))}:", *(f"  {line}" for line in tail)]


def _recent_game_log_errors(
    paths: Paths, launches: list[dict[str, object]]
) -> list[str]:
    users = paths.prefix / "pfx/drive_c/users"
    if not users.is_dir():
        return []

    earliest = _launch_epoch(launches)
    latest = _launch_end_epoch(launches)
    candidates = _files_in_window(_game_log_candidates(users), earliest, latest)
    include_tail = any(_failed_launch(launch) for launch in launches)
    result: list[str] = []
    for _, candidate in sorted(candidates, reverse=True)[:3]:
        result.extend(_game_log_evidence(candidate, include_tail=include_tail))
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
    cache_home = xdg_cache_home()
    candidates = [cache_home / "envision/logs"]
    with contextlib.suppress(OSError):
        candidates.extend((Path.home() / ".var/app").glob("*/cache/envision/logs"))
    return list(dict.fromkeys(candidates))


def _envision_log_candidates(
    earliest: float | None, latest: float | None, doctor_started: float
) -> list[tuple[float, Path, bool]]:
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
    return candidates


def _envision_timestamp(line: str) -> float | None:
    try:
        value = json.loads(line).get("timestamp")
        return (
            datetime.fromisoformat(value).timestamp()
            if isinstance(value, str)
            else None
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _envision_lines_in_window(
    lines: list[str],
    earliest: float | None,
    latest: float | None,
    doctor_started: float,
) -> list[str]:
    correlated = []
    for line in lines:
        timestamp = _envision_timestamp(line)
        launch_match = (
            timestamp is not None
            and earliest is not None
            and latest is not None
            and earliest <= timestamp <= latest
        )
        doctor_match = (
            timestamp is not None and doctor_started - 5 <= timestamp <= time.time() + 5
        )
        if timestamp is None or launch_match or doctor_match:
            correlated.append(line)
    return correlated


def _recent_envision_log_errors(
    launches: list[dict[str, object]], doctor_started: float
) -> list[str]:
    earliest = _launch_epoch(launches)
    latest = _launch_end_epoch(launches)
    candidates = _envision_log_candidates(earliest, latest, doctor_started)
    result: list[str] = []
    for _modified, target, during_doctor in sorted(candidates, reverse=True)[:2]:
        try:
            lines = _tail_lines(target)[-1000:]
        except OSError:
            continue
        correlated = _envision_lines_in_window(lines, earliest, latest, doctor_started)
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
