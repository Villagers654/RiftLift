"""Keep the packaged app's DLL search paths out of launched games."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path


def run_game(command, *, cwd, stdout, env) -> int:
    frozen = getattr(sys, "frozen", False)
    if not frozen:
        return subprocess.run(
            command,
            cwd=cwd,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=env,
            check=False,
        ).returncode

    bundle = Path(sys._MEIPASS).resolve()
    # Preserve RiftLift's explicit native runtime directory in PATH, while
    # removing the Qt/Python directories added by PyInstaller's runtime hooks.
    native = bundle / "native"
    clean_env = env.copy()
    clean_env["PATH"] = os.pathsep.join(
        part
        for part in env.get("PATH", "").split(os.pathsep)
        if part
        and (
            not Path(part).resolve().is_relative_to(bundle)
            or Path(part).resolve() == native
        )
    )
    for name in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        if name in clean_env and Path(clean_env[name]).resolve().is_relative_to(bundle):
            del clean_env[name]
    kernel = ctypes.windll.kernel32
    original = ctypes.create_unicode_buffer(32768)
    length = kernel.GetDllDirectoryW(len(original), original)
    if not kernel.SetDllDirectoryW(None):
        raise ctypes.WinError()
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=clean_env,
        )
    finally:
        kernel.SetDllDirectoryW(original.value if length else None)
    return process.wait()
