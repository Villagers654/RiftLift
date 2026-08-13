from __future__ import annotations

import fcntl
import json
import os
import re
import time
import uuid
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
_SECRET = re.compile(
    r"(?i)\b((?:"
    r"(?:(?:access|refresh|request|profile|client|native_sso)[_-]?)?token|"
    r"password|passwd|secret|authorization|cookie|session|api[_-]?key"
    r")[\"']?\s*[:=]\s*[\"']?)(?:bearer\s+)?([^\"'\s,;}&]+)"
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_WINDOWS_USER = re.compile(r"(?i)([A-Z]:\\users\\)[^\\\s]+")


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
        with path.open("r+b") as stream:
            marker = b"\n[middle diagnostic output truncated]\n"
            head_size = max_bytes // 4
            tail_size = max_bytes - head_size - len(marker)
            head = stream.read(head_size)
            stream.seek(-tail_size, os.SEEK_END)
            tail = stream.read(tail_size)
            _partial, separator, tail = tail.partition(b"\n")
            if not separator:
                tail = tail[-tail_size:]
            stream.seek(0)
            stream.write(head)
            stream.write(marker)
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
    }


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
