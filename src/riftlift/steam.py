from __future__ import annotations

import binascii
import json
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


def _shortcut(
    game: Game, launcher: Path, app_id: int | None = None, last_played: int = 0
) -> dict[str, Any]:
    executable = f'"{launcher}"'
    tags = ["VR", "RiftLift", *game.genres]
    return {
        "appid": app_id or shortcut_app_id(executable, game.name),
        "appname": game.name,
        "exe": executable,
        "StartDir": f'"{game.game_dir}"',
        "icon": game.artwork.get("icon", ""),
        "ShortcutPath": "",
        "LaunchOptions": f"launch {game.slug}",
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 1,
        "Devkit": 0,
        "DevkitGameID": "",
        "LastPlayTime": last_played,
        "FlatpakAppID": "",
        "tags": {str(index): value for index, value in enumerate(dict.fromkeys(tags))},
    }


def _existing_by_slug(shortcuts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in shortcuts.values():
        if not isinstance(value, dict):
            continue
        tags = value.get("tags")
        if not isinstance(tags, dict) or "RiftLift" not in tags.values():
            continue
        options = str(value.get("LaunchOptions") or "").split()
        if len(options) >= 2 and options[0] == "launch":
            result[options[1]] = value
    return result


def _shortcut_games(paths: Paths) -> list[Game]:
    return [game for game in games(paths) if not game.app_key.startswith("steam.app.")]


def _install_artwork(game: Game, app_id: int, config: Path) -> None:
    grid = config / "grid"
    grid.mkdir(parents=True, exist_ok=True)
    names = {
        "grid": f"{app_id}.png",
        "portrait": f"{app_id}p.png",
        "hero": f"{app_id}_hero.png",
        "logo": f"{app_id}_logo.png",
        "icon": f"{app_id}_icon.png",
    }
    for kind, filename in names.items():
        source = Path(game.artwork.get(kind, ""))
        if source.is_file():
            shutil.copy2(source, grid / filename)


def _install_wayvr_metadata(game: Game, app_id: int) -> None:
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "wayvr"
    if not cache.is_dir() and shutil.which("wayvr") is None:
        return
    cover = Path(game.artwork.get("portrait", ""))
    if cover.is_file():
        target = cache / "cover_arts" / f"{app_id}.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cover, target)
    details = {
        "type": "game",
        "name": game.name,
        "is_free": False,
        "detailed_description": game.description,
        "short_description": game.description.split("\n\n", 1)[0],
        "developers": [game.developer] if game.developer else [],
        "publishers": [game.publisher] if game.publisher else [],
        "genres": game.genres,
        "store_url": game.store_url,
    }
    target = cache / "app_details" / f"{app_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(details, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, target)


def _steam_running() -> bool:
    for target in Path("/proc").glob("[0-9]*/comm"):
        try:
            if target.read_text().strip() in {"steam", "steamwebhelper"}:
                return True
        except OSError:
            pass
    return False


def _steam_client_ready(root: Path | None = None) -> bool:
    """Return whether Steam's recorded main process is still alive."""
    candidates = [
        Path.home() / ".steam/steam.pid",
        Path.home() / ".local/share/Steam/steam.pid",
        Path.home() / ".var/app/com.valvesoftware.Steam/.steam/steam.pid",
        Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam/steam.pid",
    ]
    if root is not None:
        candidates.append(root / "steam.pid")
    for candidate in candidates:
        try:
            pid = int(candidate.read_text())
        except (OSError, ValueError):
            continue
        try:
            if (Path("/proc") / str(pid) / "comm").read_text().strip() == "steam":
                return True
        except OSError:
            continue
    # Steam can replace its main process while updating or recovering from a
    # crash before steam.pid catches up. A same-user main process is stronger
    # readiness evidence than starting a second client and perturbing a live
    # VR session; steamwebhelper alone is deliberately insufficient.
    for comm in Path("/proc").glob("[0-9]*/comm"):
        try:
            if comm.read_text().strip() != "steam":
                continue
            status = comm.with_name("status").read_text()
            uid_line = next(
                line for line in status.splitlines() if line.startswith("Uid:")
            )
            if int(uid_line.split()[1]) == os.getuid():
                return True
        except (OSError, StopIteration, ValueError):
            continue
    return False


def ensure_steam_running(timeout: float = 30.0) -> None:
    """Start Steam before a Steamworks title can escape RiftLift's launch.

    Some games call ``SteamAPI_RestartAppIfNecessary``. If Steam is closed,
    that call exits the prepared process and asks the client to relaunch a
    plain copy without RiftLift's XR environment. Starting the client first
    keeps the original process—and its native XR bridge—authoritative.
    """
    if _steam_client_ready():
        return
    steam = shutil.which("steam")
    if not steam:
        raise RiftLiftError(
            "Steam is not running and its launcher is not on PATH; start Steam and retry"
        )
    print("Starting Steam before the Steamworks game...")
    subprocess.Popen(
        (steam, "-silent"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _steam_client_ready():
            return
        time.sleep(0.2)
    raise RiftLiftError("Steam did not become ready in time; start Steam and retry")


def sync(
    paths: Paths, launcher: Path | None = None, *, allow_running: bool = False
) -> Path:
    if _steam_running() and not allow_running:
        raise RiftLiftError(
            "Steam is running. Exit Steam completely, run 'riftlift steam-sync', then reopen it."
        )
    launcher = (launcher or Path.home() / ".local/bin/riftlift").expanduser()
    if not launcher.is_absolute():
        launcher = launcher.absolute()
    target = user_config() / "shortcuts.vdf"
    document: dict[str, Any] = {"shortcuts": {}}
    if target.is_file():
        try:
            document = loads(target.read_bytes())
        except (OSError, VdfError) as error:
            raise RiftLiftError(
                f"cannot read Steam shortcuts safely: {error}"
            ) from error
    shortcuts = document.setdefault("shortcuts", {})
    if not isinstance(shortcuts, dict):
        raise RiftLiftError("Steam shortcuts file has an unexpected structure")

    existing = _existing_by_slug(shortcuts)

    retained = [
        value
        for value in shortcuts.values()
        if not (
            isinstance(value, dict)
            and (
                value.get("exe") == f'"{launcher}"'
                or (
                    isinstance(value.get("tags"), dict)
                    and "RiftLift" in value["tags"].values()
                )
            )
        )
    ]
    installed_games = _shortcut_games(paths)
    new_shortcuts = []
    for game in installed_games:
        prior = existing.get(game.slug, {})
        app_id = int(prior.get("appid") or game.steam_app_id or 0) or None
        shortcut = _shortcut(
            game, launcher, app_id, int(prior.get("LastPlayTime") or 0)
        )
        game.steam_app_id = int(shortcut["appid"])
        new_shortcuts.append(shortcut)
    retained.extend(new_shortcuts)
    document["shortcuts"] = {str(index): value for index, value in enumerate(retained)}

    target.parent.mkdir(parents=True, exist_ok=True)
    for game in installed_games:
        game.save(paths)
        _install_artwork(game, game.steam_app_id, target.parent)
        _install_wayvr_metadata(game, game.steam_app_id)
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
            raise RiftLiftError(
                "Steam is running but its launcher is not on PATH; exit it and retry"
            )
        print("Restarting Steam once so it can safely import the RiftLift shortcut...")
        subprocess.run(
            (steam, "-shutdown"),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            if not _steam_running():
                break
            time.sleep(0.2)
        else:
            raise RiftLiftError(
                "Steam did not exit in time; exit it manually and run 'riftlift steam-sync'"
            )
    target = sync(paths, launcher)
    if was_running and steam:
        subprocess.Popen(
            (steam, "-silent"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    return target
