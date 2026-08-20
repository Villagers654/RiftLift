from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from pathlib import Path


class RiftLiftError(RuntimeError):
    """A concise, user-actionable RiftLift failure."""


def atomic_write_bytes(target: Path, payload: bytes, mode: int = 0o600) -> None:
    """Atomically replace *target* using a unique file in the same directory."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}-", dir=target.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(target: Path, value: str, mode: int = 0o600) -> None:
    atomic_write_bytes(target, value.encode("utf-8"), mode)


def command(name: str) -> str:
    value = shutil.which(name)
    if not value:
        raise RiftLiftError(f"required command is missing: {name}")
    return value


def run(
    arguments: Iterable[str | os.PathLike[str]], **kwargs: object
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [os.fspath(value) for value in arguments], check=True, text=True, **kwargs
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: Path, expected_sha256: str = "") -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and (not expected_sha256 or sha256(target) == expected_sha256):
        return target
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        request = urllib.request.Request(url, headers={"User-Agent": "RiftLift/0.1"})
        for attempt in range(4):
            stream.seek(0)
            stream.truncate()
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    shutil.copyfileobj(response, stream)
                break
            except (OSError, urllib.error.URLError, TimeoutError) as error:
                if attempt == 3:
                    temporary.unlink(missing_ok=True)
                    raise RiftLiftError(
                        f"could not download {target.name} after 4 attempts: {error}"
                    ) from error
                time.sleep(2**attempt)
    actual = sha256(temporary)
    if expected_sha256 and actual != expected_sha256:
        temporary.unlink(missing_ok=True)
        raise RiftLiftError(
            f"checksum mismatch for {target.name}: expected {expected_sha256}, got {actual}"
        )
    temporary.chmod(0o644)
    temporary.replace(target)
    return target


def linux_to_windows(path: Path) -> str:
    absolute = path.expanduser().resolve()
    return "Z:" + str(absolute).replace("/", "\\")
