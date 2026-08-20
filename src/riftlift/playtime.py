from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Paths

_VERSION = 1
_CHECKPOINT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class Playtime:
    seconds: float = 0.0
    launches: int = 0
    last_played_at: str = ""


def _target(paths: Paths) -> Path:
    return paths.data / "playtime.json"


def _lock_target(paths: Paths) -> Path:
    return paths.data / "playtime.lock"


def _nonnegative_float(value: object) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _read(paths: Paths) -> dict[str, Any]:
    try:
        value = json.loads(_target(paths).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return {"version": _VERSION, "games": {}}
    if not isinstance(value, dict) or not isinstance(value.get("games"), dict):
        return {"version": _VERSION, "games": {}}
    return value


def _write(paths: Paths, value: dict[str, Any]) -> None:
    paths.data.mkdir(parents=True, exist_ok=True)
    target = _target(paths)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".playtime-", suffix=".tmp", dir=paths.data
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _update(
    paths: Paths, slug: str, operation: Callable[[dict[str, Any]], None]
) -> None:
    paths.data.mkdir(parents=True, exist_ok=True)
    with _lock_target(paths).open("a+", encoding="utf-8") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = _read(paths)
        games = value.setdefault("games", {})
        record = games.setdefault(slug, {})
        if not isinstance(record, dict):
            record = {}
            games[slug] = record
        operation(record)
        value["version"] = _VERSION
        _write(paths, value)
        fcntl.flock(lock, fcntl.LOCK_UN)


def playtime(paths: Paths, slug: str) -> Playtime:
    record = _read(paths).get("games", {}).get(slug, {})
    if not isinstance(record, dict):
        return Playtime()
    return Playtime(
        seconds=_nonnegative_float(record.get("seconds", 0.0)),
        launches=_nonnegative_int(record.get("launches", 0)),
        last_played_at=str(record.get("last_played_at", "")),
    )


def mark_launch(paths: Paths, slug: str, at: str | None = None) -> None:
    timestamp = at or datetime.now(timezone.utc).isoformat(timespec="seconds")

    def operation(record: dict[str, Any]) -> None:
        record["launches"] = _nonnegative_int(record.get("launches", 0)) + 1
        record["last_played_at"] = timestamp
        record["seconds"] = _nonnegative_float(record.get("seconds", 0.0))

    _update(paths, slug, operation)


def add_playtime(paths: Paths, slug: str, seconds: float) -> None:
    elapsed = max(0.0, float(seconds))
    if elapsed == 0.0:
        return

    def operation(record: dict[str, Any]) -> None:
        record["seconds"] = _nonnegative_float(record.get("seconds", 0.0)) + elapsed
        record["launches"] = _nonnegative_int(record.get("launches", 0))
        record["last_played_at"] = str(record.get("last_played_at", ""))

    _update(paths, slug, operation)


def format_playtime(seconds: float) -> str:
    total_minutes = max(0, int(float(seconds) // 60))
    if total_minutes == 0:
        return "< 1m"
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def playtime_label(value: Playtime) -> str:
    if value.launches == 0:
        return "Not played yet"
    return f"{format_playtime(value.seconds)} played"


class PlaytimeSession:
    """Periodically persist a running game's elapsed time."""

    def __init__(
        self,
        paths: Paths,
        slug: str,
        *,
        checkpoint_seconds: float = _CHECKPOINT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        background: bool = True,
    ):
        self.paths = paths
        self.slug = slug
        self.checkpoint_seconds = checkpoint_seconds
        self.clock = clock
        self.last_checkpoint = clock()
        self.guard = threading.Lock()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.closed = False
        mark_launch(paths, slug)
        if background:
            self.thread = threading.Thread(
                target=self._checkpoint_loop,
                daemon=True,
                name=f"riftlift-playtime-{slug}",
            )
            self.thread.start()

    def checkpoint(self) -> None:
        with self.guard:
            if self.closed:
                return
            now = self.clock()
            elapsed = max(0.0, now - self.last_checkpoint)
            add_playtime(self.paths, self.slug, elapsed)
            self.last_checkpoint = now

    def _checkpoint_loop(self) -> None:
        while not self.stop.wait(self.checkpoint_seconds):
            try:
                self.checkpoint()
            except OSError:
                # A later checkpoint or the exact final write can still recover.
                continue

    def close(self) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=max(1.0, self.checkpoint_seconds))
        with self.guard:
            if self.closed:
                return
            now = self.clock()
            elapsed = max(0.0, now - self.last_checkpoint)
            add_playtime(self.paths, self.slug, elapsed)
            self.last_checkpoint = now
            self.closed = True

    def __enter__(self) -> PlaytimeSession:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()
