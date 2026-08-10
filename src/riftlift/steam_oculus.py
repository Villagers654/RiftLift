from __future__ import annotations

import re
from pathlib import Path

from .config import Game
from .library import slugify
from .steam import steam_root
from .util import RiftLiftError


def _quoted_fields(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}
    return dict(re.findall(r'"([^"\\]+)"\s+"([^"\\]*(?:\\.[^"\\]*)*)"', text))


def steam_library_roots(root: Path | None = None) -> list[Path]:
    root = (root or steam_root()).resolve()
    result = [root]
    fields = _quoted_fields(root / "steamapps/libraryfolders.vdf")
    for key, value in fields.items():
        if not key.isdecimal():
            continue
        candidate = Path(value.replace("\\\\", "\\")).expanduser().resolve()
        if candidate not in result:
            result.append(candidate)
    return result


def _game_from_manifest(manifest: Path) -> Game | None:
    fields = _quoted_fields(manifest)
    app_id = fields.get("appid", "")
    install_dir = fields.get("installdir", "")
    if not app_id.isdecimal() or not install_dir:
        return None
    directory = manifest.parent / "common" / install_dir
    for ovr_plugin in directory.glob("*_Data/Plugins/x86_64/OVRPlugin.dll"):
        plugin_dir = ovr_plugin.parent
        if not (plugin_dir / "OculusXRPlugin.dll").is_file():
            continue
        executable = (
            directory / f"{ovr_plugin.parents[2].name.removesuffix('_Data')}.exe"
        )
        if not executable.is_file():
            continue
        name = fields.get("name") or install_dir
        return Game(
            slug=slugify(name),
            name=name,
            app_id=app_id,
            app_key=f"steam.app.{app_id}",
            directory=str(directory.resolve()),
            executable=executable.name,
            arguments=[],
            version=fields.get("buildid", ""),
            store_url=f"https://store.steampowered.com/app/{app_id}/",
            steam_app_id=int(app_id),
        )
    return None


def steam_oculus_games(root: Path | None = None) -> list[Game]:
    result: list[Game] = []
    for library in steam_library_roots(root):
        for manifest in sorted((library / "steamapps").glob("appmanifest_*.acf")):
            game = _game_from_manifest(manifest)
            if game:
                result.append(game)
    return result


def steam_oculus_game(app_id: str, root: Path | None = None) -> Game:
    if not app_id.isdecimal():
        raise ValueError(f"invalid Steam app ID: {app_id!r}")
    for game in steam_oculus_games(root):
        if game.app_id == app_id:
            return game
    raise RiftLiftError(
        f"Steam app {app_id} is not an installed 64-bit Unity Oculus XR title"
    )
