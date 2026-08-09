from __future__ import annotations

import shutil
from pathlib import Path

from .config import Paths, games
from .runtime import META_PACKAGES, active_runtime_json, proton_dir
from .steam import steam_root


def doctor(paths: Paths) -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(label: str, action: object) -> None:
        try:
            value = action() if callable(action) else action
            checks.append((label, True, str(value)))
        except Exception as error:
            checks.append((label, False, str(error)))

    check("Active OpenXR runtime", active_runtime_json)
    check("Steam", steam_root)
    check("GE-Proton", lambda: (proton_dir() / "proton") if (proton_dir() / "proton").is_file() else (_ for _ in ()).throw(FileNotFoundError("not installed")))
    check("ReviveXR", lambda: paths.tools / "revive/LibReviveXR64.dll" if (paths.tools / "revive/LibReviveXR64.dll").is_file() else (_ for _ in ()).throw(FileNotFoundError("not installed")))
    check("Meta client", lambda: paths.prefix / "pfx/drive_c/Program Files/Oculus/Support/oculus-client/Client.exe" if (paths.prefix / "pfx/drive_c/Program Files/Oculus/Support/oculus-client/Client.exe").is_file() else (_ for _ in ()).throw(FileNotFoundError("not installed")))
    platform_files = (
        paths.tools / "platform-compat/LibOVRPlatform64_1.dll",
        paths.tools / "platform-compat/LibOVRPlatformImpl64_1.dll",
        paths.tools / "platform-compat/LibOVRPlatformImpl64_1_real.dll",
    )
    check("Platform bridge", lambda: platform_files[0].parent if all(path.is_file() for path in platform_files) else (_ for _ in ()).throw(FileNotFoundError("incomplete")))
    for game in games(paths):
        checks.append((f"Game: {game.name}", game.executable_path.is_file(), str(game.executable_path)))

    width = max(len(label) for label, _, _ in checks)
    for label, passed, detail in checks:
        print(f"{'OK' if passed else 'FAIL':4}  {label:<{width}}  {detail}")
    print(f"\nPinned Meta Horizon Link packages: {len(META_PACKAGES)} (version 205.0)")
    return 0 if all(passed for _, passed, _ in checks) else 1
