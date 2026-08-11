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

from .config import Game, Paths

_MAX_HISTORY_BYTES = 256 * 1024
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
) -> tuple[str, float]:
    launch_id = uuid.uuid4().hex[:12]
    started = time.monotonic()
    _append(
        paths,
        {
            "id": launch_id,
            "event": "started",
            "at": utc_now(),
            "game": game.name,
            "slug": game.slug,
            "source": game.source,
            "backend": backend,
            "executable": game.executable_path.name,
            "capabilities": capabilities,
            "wrapper": wrapper,
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
    record: dict[str, Any] = {
        "id": launch_id,
        "event": "finished",
        "at": utc_now(),
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
            launches[launch_id] = dict(event)
            order.append(launch_id)
        elif launch_id in launches and event.get("event") == "finished":
            launches[launch_id].update(event)

    completed = [launches[item] for item in order if item in launches]
    failures = [
        item
        for item in completed
        if item.get("event") != "finished"
        or item.get("exit_code") not in (0, None)
        or item.get("error")
    ]
    selected = failures[-limit:]
    for item in reversed(completed):
        if item not in selected:
            selected.append(item)
        if len(selected) >= limit:
            break
    selected.sort(key=completed.index, reverse=True)
    return selected[:limit]
