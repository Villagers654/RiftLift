from __future__ import annotations

import fcntl
import json
import os
import platform
import re
import shutil
import subprocess
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .config import Game, Paths

_MAX_HISTORY_BYTES = 256 * 1024
_MAX_LAUNCH_LOGS = 10
_MAX_LAUNCH_LOG_BYTES = 3 * 1024 * 1024
_MAX_PROTON_LOGS = 5
_MAX_PROTON_LOG_BYTES = 8 * 1024 * 1024
_MAX_GRAPHICS_LOGS = 10
_MAX_GRAPHICS_LOG_BYTES = 2 * 1024 * 1024
_MAX_CRASH_LOGS = 5
_MAX_CRASH_LOG_BYTES = 6 * 1024 * 1024
_MAX_OPENVR_LOGS = 8
_MAX_OPENVR_LOG_BYTES = 4 * 1024 * 1024
_MAX_RUNTIME_TRACE_BYTES = 12 * 1024 * 1024
_MAX_GAME_LOGS = 8
_MAX_GAME_LOG_BYTES = 4 * 1024 * 1024
_SECRET = re.compile(
    r"(?i)\b((?:"
    r"(?:(?:access|refresh|request|profile|client|native_sso)[_-]?)?token|"
    r"password|passwd|secret|authorization|cookie|session|api[_-]?key"
    r")[\"']?\s*[:=]\s*[\"']?)(?:bearer\s+)?([^\"'\s,;}&]+)"
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_WINDOWS_USER = re.compile(r"(?i)([A-Z]:\\users\\)[^\\\s]+")
_DIAGNOSTIC_EVIDENCE = re.compile(
    rb"(?i)(?:riftlift|xrizer|debugstr:|:err:|:warn:|fatal|panic|exception|"
    rb"failed|failure|\berror\b|xr_error|vk_error|device[_ ]lost|"
    rb"ntcreateuserprocess)"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact(value: str) -> str:
    """Remove credentials and user-specific paths from public diagnostics."""
    homes = {str(Path.home())}
    try:
        homes.add(str(Path.home().resolve()))
    except OSError:
        pass
    homes.update(f"/var{home}" for home in tuple(homes) if home.startswith("/home/"))
    result = value
    for home in sorted(homes, key=len, reverse=True):
        if home:
            result = result.replace(home, "~")
    result = _WINDOWS_USER.sub(r"\1<user>", result)
    result = _SECRET.sub(lambda match: f"{match.group(1)}<redacted>", result)
    return _EMAIL.sub("<redacted-email>", result)


def _history_path(paths: Paths) -> Path:
    return paths.data / "diagnostics" / "launches.jsonl"


def launch_log_path(paths: Paths, launch_id: str) -> Path:
    return paths.data / "diagnostics" / "logs" / f"launch-{launch_id}.log"


def trim_diagnostic_log(path: Path, max_bytes: int) -> None:
    try:
        size = path.stat().st_size
        if size <= max_bytes:
            return
        evidence = b""
        if max_bytes >= 4096:
            first: list[bytes] = []
            last: deque[bytes] = deque(maxlen=160)
            recent: deque[bytes] = deque()
            recent_set: set[bytes] = set()
            with path.open("rb") as source:
                for line in source:
                    if not _DIAGNOSTIC_EVIDENCE.search(line):
                        continue
                    clipped = line[:2000].rstrip(b"\r\n") + b"\n"
                    # A failed capability query can be repeated every frame.
                    # Keep one copy while still allowing the same message to
                    # reappear much later, where it may describe a new event.
                    if clipped in recent_set:
                        continue
                    recent.append(clipped)
                    recent_set.add(clipped)
                    if len(recent) > 512:
                        recent_set.remove(recent.popleft())
                    if len(first) < 48:
                        first.append(clipped)
                    else:
                        last.append(clipped)
            selected = b"".join([*first, *last])
            evidence_budget = min(max_bytes // 4, 512 * 1024)
            if len(selected) > evidence_budget:
                first_size = evidence_budget // 3
                evidence = (
                    selected[:first_size]
                    + b"\n[selected evidence abbreviated]\n"
                    + selected[-(evidence_budget - first_size - 34) :]
                )
            else:
                evidence = selected
        with path.open("r+b") as stream:
            marker = b"\n[middle diagnostic output truncated]\n"
            evidence_block = (
                b"\n[selected diagnostic evidence preserved]\n"
                + evidence
                + b"[end selected diagnostic evidence]\n"
                if evidence
                else b""
            )
            head_size = max_bytes // 5 if evidence_block else max_bytes // 4
            tail_size = max_bytes - head_size - len(marker) - len(evidence_block)
            head = stream.read(head_size)
            stream.seek(-tail_size, os.SEEK_END)
            tail = stream.read(tail_size)
            _partial, separator, tail = tail.partition(b"\n")
            if not separator:
                tail = tail[-tail_size:]
            stream.seek(0)
            stream.write(head)
            stream.write(marker)
            stream.write(evidence_block)
            stream.write(tail)
            stream.truncate()
    except OSError:
        pass


def prune_diagnostic_logs(
    directory: Path, pattern: str, *, keep: int, max_bytes: int
) -> None:
    try:
        candidates = sorted(
            (item for item in directory.glob(pattern) if item.is_file()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for item in candidates[:keep]:
            trim_diagnostic_log(item, max_bytes)
        for item in candidates[keep:]:
            item.unlink(missing_ok=True)
    except OSError:
        pass


def prepare_launch_log(paths: Paths, launch_id: str) -> Path:
    target = launch_log_path(paths, launch_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    prune_diagnostic_logs(
        target.parent,
        "launch-*.log",
        keep=_MAX_LAUNCH_LOGS - 1,
        max_bytes=_MAX_LAUNCH_LOG_BYTES,
    )
    return target


def finish_launch_log(path: Path) -> None:
    trim_diagnostic_log(path, _MAX_LAUNCH_LOG_BYTES)


def prepare_proton_logs(paths: Paths) -> Path:
    directory = paths.data / "diagnostics" / "proton"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    prune_diagnostic_logs(
        directory,
        "*.log",
        keep=_MAX_PROTON_LOGS,
        max_bytes=_MAX_PROTON_LOG_BYTES,
    )
    return directory


def _prepare_debug_log_directory(
    paths: Paths, name: str, *, keep: int, max_bytes: int
) -> Path:
    directory = paths.data / "diagnostics" / name
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    prune_diagnostic_logs(directory, "*", keep=keep, max_bytes=max_bytes)
    return directory


def prepare_graphics_logs(paths: Paths) -> Path:
    return _prepare_debug_log_directory(
        paths,
        "graphics",
        keep=_MAX_GRAPHICS_LOGS,
        max_bytes=_MAX_GRAPHICS_LOG_BYTES,
    )


def prepare_crash_logs(paths: Paths) -> Path:
    return _prepare_debug_log_directory(
        paths,
        "crashes",
        keep=_MAX_CRASH_LOGS,
        max_bytes=_MAX_CRASH_LOG_BYTES,
    )


def prepare_debug_logs(paths: Paths) -> dict[str, Path]:
    return {
        "proton": prepare_proton_logs(paths),
        "graphics": prepare_graphics_logs(paths),
        "crashes": prepare_crash_logs(paths),
        "openvr": _prepare_debug_log_directory(
            paths,
            "openvr",
            keep=_MAX_OPENVR_LOGS,
            max_bytes=_MAX_OPENVR_LOG_BYTES,
        ),
        "game": _prepare_debug_log_directory(
            paths,
            "game",
            keep=_MAX_GAME_LOGS,
            max_bytes=_MAX_GAME_LOG_BYTES,
        ),
    }


def _game_log_candidates(game: Game) -> list[Path]:
    """Find game-owned text logs without crawling an entire large install."""
    result: list[Path] = []
    root_depth = len(game.game_dir.parts)
    visited = 0
    ignored = {
        "assets",
        "content",
        "movies",
        "pak",
        "paks",
        "shaders",
        "textures",
    }
    try:
        for root, directories, files in os.walk(game.game_dir):
            visited += 1
            depth = len(Path(root).parts) - root_depth
            if depth >= 5 or visited >= 500:
                directories[:] = []
            else:
                directories[:] = [
                    item for item in directories if item.casefold() not in ignored
                ]
            log_directory = "log" in Path(root).name.casefold()
            for name in files:
                lowered = name.casefold()
                if lowered in {
                    "player.log",
                    "output_log.txt",
                    "crash.log",
                    "error.log",
                } or (log_directory and lowered.endswith((".log", ".txt"))):
                    result.append(Path(root) / name)
            if visited >= 500:
                break
    except OSError:
        pass
    return result


def _copy_bounded_log(source: Path, target: Path, max_bytes: int) -> None:
    """Preserve context and the newest failure without a large temporary copy."""
    size = source.stat().st_size
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if size <= max_bytes:
        shutil.copyfile(source, target)
        return
    marker = b"\n[middle game log output truncated by RiftLift]\n"
    head_size = max_bytes // 4
    tail_size = max_bytes - head_size - len(marker)
    with source.open("rb") as incoming, target.open("wb") as outgoing:
        outgoing.write(incoming.read(head_size))
        outgoing.write(marker)
        incoming.seek(-tail_size, os.SEEK_END)
        tail = incoming.read(tail_size)
        _partial, separator, tail = tail.partition(b"\n")
        outgoing.write(tail if separator else tail[-tail_size:])


def collect_game_logs(
    paths: Paths, game: Game, launch_id: str, launched_at: float
) -> None:
    """Snapshot logs produced by this debug launch for later doctor reports."""
    directory = _prepare_debug_log_directory(
        paths,
        "game",
        keep=_MAX_GAME_LOGS,
        max_bytes=_MAX_GAME_LOG_BYTES,
    )
    recent: list[tuple[float, Path]] = []
    for candidate in _game_log_candidates(game):
        try:
            modified = candidate.stat().st_mtime
        except OSError:
            continue
        if modified >= launched_at - 5:
            recent.append((modified, candidate))
    for index, (_modified, source) in enumerate(sorted(recent, reverse=True)[:3]):
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.name)[-100:]
        target = directory / f"{launch_id}-{index}-{safe_name}"
        try:
            _copy_bounded_log(source, target, _MAX_GAME_LOG_BYTES)
        except OSError:
            target.unlink(missing_ok=True)
    prune_diagnostic_logs(
        directory, "*", keep=_MAX_GAME_LOGS, max_bytes=_MAX_GAME_LOG_BYTES
    )


def system_build_components(*, probe_vulkan: bool = True) -> dict[str, str]:
    """Compact host-version snapshot shared by launch history and doctor."""
    distro = "unknown"
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key == "PRETTY_NAME":
                distro = value.strip().strip('"')
                break
    except OSError:
        pass
    libc_name, libc_version = platform.libc_ver()
    vulkan = "unavailable"
    if probe_vulkan:
        try:
            result = subprocess.run(
                ["vulkaninfo", "--summary"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=6,
            )
            details = []
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped.startswith(("driverName", "driverInfo", "apiVersion")):
                    details.append(stripped)
            if details:
                vulkan = "; ".join(details[:6])[:600]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {
        "system_os": distro,
        "system_kernel": f"{platform.release()} {platform.machine()}",
        "system_python": platform.python_version(),
        "system_libc": f"{libc_name or 'unknown'} {libc_version or 'unknown'}",
        "system_vulkan": vulkan,
    }


def runtime_trace_paths(paths: Paths) -> list[Path]:
    users = paths.prefix / "pfx/drive_c/users"
    result: list[Path] = []
    for pattern in (
        "*/Temp/riftlift-runtime-trace.log",
        "*/AppData/Local/Temp/riftlift-runtime-trace.log",
    ):
        result.extend(item for item in users.glob(pattern) if item.is_file())
    return list(dict.fromkeys(result))


def clear_runtime_traces(paths: Paths) -> None:
    for trace in runtime_trace_paths(paths):
        trace.unlink(missing_ok=True)


def trim_runtime_traces(paths: Paths) -> None:
    for trace in runtime_trace_paths(paths):
        trim_diagnostic_log(trace, _MAX_RUNTIME_TRACE_BYTES)


def _append(paths: Paths, record: dict[str, Any]) -> None:
    target = _history_path(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0, os.SEEK_END)
        if stream.tell() > _MAX_HISTORY_BYTES:
            stream.seek(0)
            lines = stream.readlines()[-400:]
            stream.seek(0)
            stream.truncate()
            stream.writelines(lines)
        stream.write(
            json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
        )
        stream.flush()
        fcntl.flock(stream, fcntl.LOCK_UN)


def launch_started(
    paths: Paths,
    game: Game,
    backend: str,
    *,
    wrapper: bool,
    capabilities: list[str],
    debug_logging: bool = False,
    debug_settings: dict[str, str] | None = None,
    components: dict[str, str] | None = None,
    expected_components: dict[str, str] | None = None,
) -> tuple[str, float]:
    launch_id = uuid.uuid4().hex[:12]
    started = time.monotonic()
    started_at = utc_now()
    _append(
        paths,
        {
            "id": launch_id,
            "event": "started",
            "at": started_at,
            "started_at": started_at,
            "game": game.name,
            "slug": game.slug,
            "source": game.source,
            "backend": backend,
            "executable": game.executable_path.name,
            "capabilities": capabilities,
            "wrapper": wrapper,
            "debug_logging": debug_logging,
            "debug_settings": debug_settings or {},
            "riftlift_version": __version__,
            "components": components or {"riftlift": __version__},
            "expected_components": expected_components or {},
        },
    )
    return launch_id, started


def launch_finished(
    paths: Paths,
    launch_id: str,
    started: float,
    *,
    exit_code: int | None = None,
    error: str = "",
) -> None:
    finished_at = utc_now()
    record: dict[str, Any] = {
        "id": launch_id,
        "event": "finished",
        "at": finished_at,
        "finished_at": finished_at,
        "duration_seconds": round(max(0.0, time.monotonic() - started), 1),
    }
    if exit_code is not None:
        record["exit_code"] = exit_code
    if error:
        record["error"] = redact(error)[:500]
    _append(paths, record)


def recent_launches(paths: Paths, limit: int = 5) -> list[dict[str, Any]]:
    try:
        lines = _history_path(paths).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    launches: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for line in lines[-800:]:
        try:
            event = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        launch_id = event.get("id")
        if not isinstance(launch_id, str):
            continue
        if event.get("event") == "started":
            launch = dict(event)
            launch["started_at"] = event.get("started_at", event.get("at", ""))
            launches[launch_id] = launch
            order.append(launch_id)
        elif launch_id in launches and event.get("event") == "finished":
            # Keep `at` as the launch time for display and journal correlation.
            # Older history only has `at`, so normalize it while reading rather
            # than requiring a migration of users' JSONL files.
            finished = dict(event)
            finished["finished_at"] = event.get("finished_at", event.get("at", ""))
            finished.pop("at", None)
            launches[launch_id].update(finished)

    completed = [launches[item] for item in order if item in launches]
    # Keep this section genuinely recent. Prioritizing every historical failure
    # makes a repaired launch look broken forever and keeps stale remediation in
    # future doctor reports.
    return list(reversed(completed[-limit:]))
