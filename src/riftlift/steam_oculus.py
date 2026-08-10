from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from .config import Game, Paths, games
from .detection import (
    best_windows_executable,
    is_unreal_shipping,
    uses_oculus_runtime,
    uses_openvr_runtime,
)
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
    if not directory.is_dir() or not uses_oculus_runtime(directory):
        return None
    try:
        executable = best_windows_executable(directory)
    except ValueError:
        return None
    name = fields.get("name") or install_dir
    return Game(
        slug=slugify(name),
        name=name,
        app_id=app_id,
        app_key=f"steam.app.{app_id}",
        directory=str(directory.resolve()),
        executable=executable.relative_to(directory.resolve()).as_posix(),
        arguments=["-vr"] if is_unreal_shipping(executable) else [],
        version=fields.get("buildid", ""),
        platform_shim=False,
        store_url=f"https://store.steampowered.com/app/{app_id}/",
        steam_app_id=int(app_id),
    )


def game_from_steam_command(game: Game, command: list[str]) -> Game:
    """Use Steam's expanded %command% as the authoritative launch selection."""
    values = command[1:] if command[:1] == ["--"] else command
    directory = game.game_dir.resolve()
    selected: tuple[int, Path] | None = None
    for index, value in enumerate(values):
        unquoted = value.strip('"')
        if not unquoted.casefold().endswith(".exe"):
            continue
        if unquoted.casefold().startswith("z:\\"):
            unquoted = "/" + unquoted[3:].replace("\\", "/")
        candidate = Path(unquoted).expanduser()
        if not candidate.is_absolute():
            candidate = directory / candidate
        try:
            candidate = candidate.resolve()
            candidate.relative_to(directory)
        except (OSError, ValueError):
            continue
        if candidate.is_file():
            selected = (index, candidate)
    if selected is None:
        return game
    index, executable = selected
    return replace(
        game,
        executable=executable.relative_to(directory).as_posix(),
        arguments=values[index + 1 :],
    )


def steam_command_uses_oculus(game: Game, command: list[str]) -> bool:
    """Select Revive only for the Oculus mode of a multi-runtime Steam game."""
    if not command:
        return True
    values = command[1:] if command[:1] == ["--"] else command
    if any("oculus" in value.casefold() for value in values):
        return True
    return not uses_openvr_runtime(game.game_dir)


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
        f"Steam app {app_id} is not an installed 64-bit Oculus PC runtime title"
    )


def add_steam_game(paths: Paths, game: Game) -> Game:
    existing = next(
        (installed for installed in games(paths) if installed.app_key == game.app_key),
        None,
    )
    if existing is not None:
        game = replace(game, slug=existing.slug)
    elif (paths.data / "games" / f"{game.slug}.json").exists():
        base = f"{game.slug}-steam"
        slug = base
        suffix = 2
        while (paths.data / "games" / f"{slug}.json").exists():
            slug = f"{base}-{suffix}"
            suffix += 1
        game = replace(game, slug=slug)
    game.save(paths)
    return game
