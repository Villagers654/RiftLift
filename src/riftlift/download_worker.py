"""Private JSON-lines download worker. Account secrets never enter its protocol."""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path

from .config import Paths
from .util import LineWriter


def restore_worker_streams() -> None:
    """Recover inherited pipes in pythonw and windowed PyInstaller workers."""
    if os.name != "nt":
        return
    import ctypes
    import msvcrt

    kernel = ctypes.windll.kernel32
    kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
    kernel.GetStdHandle.restype = ctypes.c_void_p
    for name, number, mode, flags in (
        ("stdin", -10, "r", os.O_RDONLY),
        ("stdout", -11, "w", os.O_WRONLY),
    ):
        if getattr(sys, name) is not None:
            continue
        handle = kernel.GetStdHandle(number & 0xFFFFFFFF)
        if not handle or handle == ctypes.c_void_p(-1).value:
            raise OSError("Download worker pipe is unavailable")
        descriptor = msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
        setattr(sys, name, os.fdopen(descriptor, mode, encoding="utf-8", buffering=1))


def main() -> int:
    restore_worker_streams()
    output = sys.stdout

    def emit(event, **values):
        output.write(json.dumps({"event": event, **values}) + "\n")
        output.flush()

    try:
        request = json.loads(sys.stdin.readline(65536))
        paths = Paths(**{key: Path(value) for key, value in request["paths"].items()})
        from .library import add, parse_download_progress

        def progress(line):
            parsed = parse_download_progress(line)
            if parsed:
                label, current, total = parsed
                emit("progress", label=label, current=current, total=total)

        with contextlib.redirect_stdout(LineWriter(progress)):
            game = add(paths, request["url"])
            emit("finishing")
            # The install record is already committed. A Steam sync failure
            # must not turn an installed game into a failed download.
            if request.get("sync_steam"):
                from .steam import sync_with_restart

                try:
                    sync_with_restart(paths)
                except Exception:
                    emit("warning", reason="steam_sync_failed")
        emit("complete", slug=game.slug)
        return 0
    except Exception as error:
        # Exceptions from HTTP clients may contain credential-bearing URLs.
        # Send a bounded classification, never their raw text or traceback.
        message = str(error).lower()
        reason = (
            "sign_in_required"
            if any(
                text in message
                for text in ("401", "403", "login", "log in", "token", "sign in")
            )
            else "download_failed"
        )
        emit("error", reason=reason)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
