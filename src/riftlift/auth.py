from __future__ import annotations

import os
import re
from pathlib import Path

from .config import Paths
from .util import RiftLiftError


_TOKEN_KEY = b"riftlift-token"
_TOKEN_PATTERN = re.compile(rb"[A-Za-z0-9_.|-]{32,4096}")


def _varint(payload: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for index in range(offset, min(offset + 10, len(payload))):
        byte = payload[index]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, index + 1
        shift += 7
    raise ValueError("invalid LevelDB varint")


def _tokens_in_leveldb_file(target: Path) -> list[str]:
    payload = target.read_bytes()
    results: list[str] = []
    offset = 0
    while (key := payload.find(_TOKEN_KEY, offset)) >= 0:
        offset = key + len(_TOKEN_KEY)
        try:
            length, value_at = _varint(payload, offset)
        except ValueError:
            continue
        # Chromium Local Storage prefixes string values with encoding byte 1.
        value = payload[value_at : value_at + length]
        if value[:1] == b"\x01" and _TOKEN_PATTERN.fullmatch(value[1:]):
            results.append(value[1:].decode("ascii"))
    return results


def _electron_token(paths: Paths) -> str | None:
    leveldb = (
        paths.prefix
        / "pfx/drive_c/users/steamuser/AppData/Roaming/Client/Local Storage/leveldb"
    )
    if not leveldb.is_dir():
        return None
    candidates = [*leveldb.glob("*.log"), *leveldb.glob("*.ldb")]
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    for target in candidates:
        try:
            tokens = _tokens_in_leveldb_file(target)
        except OSError:
            continue
        if tokens:
            return tokens[-1]
    return None


def _save(paths: Paths, token: str) -> None:
    paths.create()
    target = paths.config / "meta-access-token"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(token + "\n")
    target.chmod(0o600)


def runtime_access_token(paths: Paths, *, refresh: bool = False) -> str:
    """Return the scoped token created by Meta Horizon Link in this prefix."""
    target = paths.config / "meta-access-token"
    if not refresh:
        try:
            token = target.read_text().strip()
            if _TOKEN_PATTERN.fullmatch(token.encode("ascii")):
                return token
        except (FileNotFoundError, OSError, UnicodeError):
            pass
    token = _electron_token(paths)
    if token is None:
        raise RiftLiftError("no persistent Meta login found; run 'riftlift login' first")
    _save(paths, token)
    return token
