from __future__ import annotations

import binascii
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import Game, Paths, games
from .steam_vdf import VdfError, dumps, loads
from .util import RiftLiftError


def steam_root() -> Path:
    choices = (
        Path.home() / ".local/share/Steam",
        Path.home() / ".steam/steam",
        Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam",
    )
    for choice in choices:
        if (choice / "userdata").is_dir():
            return choice.resolve()
    raise RiftLiftError("Steam userdata was not found; start Steam once, then retry")


def user_config(root: Path | None = None) -> Path:
    root = root or steam_root()
    candidates = sorted(
        (path for path in (root / "userdata").glob("[0-9]*/config") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RiftLiftError("Steam has no local user profile; sign in once, then retry")
    return candidates[0]


def shortcut_app_id(executable: str, name: str) -> int:
    checksum = binascii.crc32((executable + name).encode("utf-8")) & 0xFFFFFFFF
    return checksum | 0x80000000


def _shortcut(game: Game, launcher: Path) -> dict[str, Any]:
    executable = f'"{launcher}"'
    return {
        "appid": shortcut_app_id(executable, game.name),
        "appname": game.name,
        "exe": executable,
        "StartDir": f'"{game.game_dir}"',
        "icon": "",
        "ShortcutPath": "",
        "LaunchOptions": f"launch {game.slug}",
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 1,
        "Devkit": 0,
        "DevkitGameID": "",
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "tags": {"0": "VR", "1": "RiftLift"},
    }


def _steam_running() -> bool:
    for target in Path("/proc").glob("[0-9]*/comm"):
        try:
            if target.read_text().strip() in {"steam", "steamwebhelper"}:
                return True
        except OSError:
            pass
    return False


def sync(paths: Paths, launcher: Path | None = None, *, allow_running: bool = False) -> Path:
    if _steam_running() and not allow_running:
        raise RiftLiftError(
            "Steam is running. Exit Steam completely, run 'riftlift steam-sync', then reopen it."
        )
    launcher = (launcher or Path.home() / ".local/bin/riftlift").resolve()
    target = user_config() / "shortcuts.vdf"
    document: dict[str, Any] = {"shortcuts": {}}
    if target.is_file():
        try:
            document = loads(target.read_bytes())
        except (OSError, VdfError) as error:
            raise RiftLiftError(f"cannot read Steam shortcuts safely: {error}") from error
    shortcuts = document.setdefault("shortcuts", {})
    if not isinstance(shortcuts, dict):
        raise RiftLiftError("Steam shortcuts file has an unexpected structure")

    retained = [
        value
        for value in shortcuts.values()
        if not (
            isinstance(value, dict)
            and (
                value.get("exe") == f'"{launcher}"'
                or "RiftLift" in (value.get("tags") or {}).values()
            )
        )
    ]
    retained.extend(_shortcut(game, launcher) for game in games(paths))
    document["shortcuts"] = {str(index): value for index, value in enumerate(retained)}

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        backup = target.with_name(f"shortcuts.vdf.riftlift-{int(time.time())}.bak")
        shutil.copy2(target, backup)
    temporary = target.with_suffix(".vdf.tmp")
    temporary.write_bytes(dumps(document))
    os.replace(temporary, target)
    return target


def sync_with_restart(paths: Paths, launcher: Path | None = None) -> Path:
    was_running = _steam_running()
    steam = shutil.which("steam")
    if was_running:
        if not steam:
            raise RiftLiftError("Steam is running but its launcher is not on PATH; exit it and retry")
        print("Restarting Steam once so it can safely import the RiftLift shortcut...")
        subprocess.run((steam, "-shutdown"), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if not _steam_running():
                break
            time.sleep(0.2)
        else:
            raise RiftLiftError("Steam did not exit in time; exit it manually and run 'riftlift steam-sync'")
    target = sync(paths, launcher)
    if was_running and steam:
        subprocess.Popen((steam, "-silent"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return target
