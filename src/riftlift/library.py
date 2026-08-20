from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from meta_pcvr_downloader.api import list_builds, parse_app_id, select_build
from meta_pcvr_downloader.download import Downloader, fetch_manifest

from .auth import runtime_access_token
from .config import Game, Paths
from .detection import best_windows_executable, is_unreal_shipping
from .metadata import generate_artwork, populate_game_metadata
from .util import RiftLiftError


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "meta-rift-game"


def default_download_workers(cpu_count: int | None = None) -> int:
    if cpu_count is None:
        try:
            cpu_count = len(os.sched_getaffinity(0))
        except AttributeError:
            cpu_count = os.cpu_count() or 1
    return max(4, min(32, cpu_count * 2))


def _path(value: str) -> Path:
    return Path(value.replace("\\", "/"))


def _split_launch_arguments(value: str) -> list[str]:
    """Split a Windows launch string without retaining quotes or eating slashes."""
    lexer = shlex.shlex(value, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    return list(lexer)


def _best_executable(directory: Path, manifest: dict, override: str | None) -> str:
    preferred = _path(override or str(manifest.get("launchFile") or ""))
    if override and not preferred.name:
        raise ValueError("--executable cannot be empty")
    if not preferred.name and override is None:
        raise ValueError("Meta manifest has no launch executable; pass --executable")
    candidate = best_windows_executable(directory, preferred)
    return candidate.relative_to(directory.resolve()).as_posix()


def _launch_arguments(
    directory: Path, executable: str, manifest: dict, override: str | None
) -> list[str]:
    value = (
        override
        if override is not None
        else str(manifest.get("launchParameters") or "")
    )
    arguments = _split_launch_arguments(value)
    if override is None and is_unreal_shipping(directory / executable):
        vr_options = {"-vr", "-oculus", "-openxr", "-steamvr"}
        if not any(argument.casefold() in vr_options for argument in arguments):
            arguments.append("-vr")
    return arguments


def add(
    paths: Paths,
    app: str,
    *,
    build_selector: str | None = None,
    executable: str | None = None,
    arguments: str | None = None,
    jobs: int | None = None,
) -> Game:
    paths.create()
    app_id = parse_app_id(app)
    print("Reading your persistent RiftLift Meta login...")
    token = runtime_access_token(paths)
    build = select_build(list_builds(token, app_id), build_selector)
    slug = slugify(build.app_name)
    directory = paths.games / slug
    print(f"Downloading {build.app_name} {build.version}...")
    manifest = fetch_manifest(token, build)
    workers = default_download_workers() if jobs is None else jobs
    print(f"Using {workers} download workers.")
    Downloader(token, build, directory, paths.cache / "segments", workers).run(manifest)
    launch_file = _best_executable(directory, manifest, executable)
    launch_arguments = _launch_arguments(directory, launch_file, manifest, arguments)
    game = Game(
        slug=slug,
        name=build.app_name,
        app_id=app_id,
        app_key=str(manifest.get("canonicalName") or slug),
        directory=str(directory.resolve()),
        executable=launch_file,
        arguments=launch_arguments,
        version=build.version,
        platform_offline=True,
        source="meta",
    )
    game.save(paths)
    try:
        populate_game_metadata(paths, game)
    except RiftLiftError as error:
        print(f"warning: catalog metadata was not available: {error}")
    return game


def _local_game_root(
    executable: Path, root: str | Path | None
) -> tuple[Path, str | None]:
    if root is not None:
        return Path(root).expanduser().resolve(), None
    if (
        executable.parent.name.casefold() == "win10"
        and executable.parent.parent.name.casefold() == "bin"
    ):
        game_root = executable.parent.parent.parent
        return game_root, game_root.name
    return executable.parent, None


def _check_local_conflict(paths: Paths, slug: str, name: str, executable: Path) -> None:
    target = paths.data / "games" / f"{slug}.json"
    if not target.exists():
        return
    existing = Game.load(paths, slug)
    if existing.executable_path.resolve() != executable:
        raise ValueError(
            f"{name!r} conflicts with existing game {existing.name!r}; "
            "choose a different name"
        )


def add_local(
    paths: Paths,
    executable: str | Path,
    *,
    name: str | None = None,
    root: str | Path | None = None,
    arguments: str | None = None,
    app_key: str | None = None,
    artwork: str | Path | None = None,
    version: str = "",
) -> Game:
    paths.create()
    executable_path = Path(executable).expanduser().resolve()
    if not executable_path.is_file():
        raise ValueError(f"local game executable was not found: {executable_path}")
    if executable_path.suffix.casefold() != ".exe":
        raise ValueError("local games must point to a Windows .exe file")

    game_root, inferred_app_key = _local_game_root(executable_path, root)
    if not game_root.is_dir():
        raise ValueError(f"local game folder was not found: {game_root}")
    try:
        relative_executable = executable_path.relative_to(game_root)
    except ValueError as error:
        raise ValueError(
            "the executable must be inside the local game folder"
        ) from error

    game_name = (name or executable_path.stem).strip()
    if not game_name:
        raise ValueError("local game name cannot be empty")
    slug = slugify(game_name)
    launch_arguments = _split_launch_arguments(arguments) if arguments else []
    game = Game(
        slug=slug,
        name=game_name,
        app_id="",
        app_key=(app_key or inferred_app_key or f"local.{slug}").strip(),
        directory=str(game_root),
        executable=relative_executable.as_posix(),
        arguments=launch_arguments,
        version=version.strip(),
        platform_shim=True,
        platform_offline=False,
        source="local",
    )
    if not game.app_key:
        raise ValueError("local game app key cannot be empty")

    _check_local_conflict(paths, slug, game_name, executable_path)

    if artwork is not None:
        artwork_path = Path(artwork).expanduser().resolve()
        if not artwork_path.is_file():
            raise ValueError(f"local artwork was not found: {artwork_path}")
        game.artwork = generate_artwork(paths, game, artwork_path.read_bytes())
    game.save(paths)
    return game
