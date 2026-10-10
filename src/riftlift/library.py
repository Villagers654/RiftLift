from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
from collections.abc import Callable
from pathlib import Path

from meta_pcvr_downloader.api import (
    MetaApiError,
    list_builds,
    parse_app_id,
    select_build,
)
from meta_pcvr_downloader.download import Downloader, DownloadError, fetch_manifest

from .auth import account_tokens
from .config import Game, Paths
from .detection import best_windows_executable, is_unreal_shipping
from .metadata import generate_artwork, populate_game_metadata
from .util import RiftLiftError

_SEGMENTS_PROGRESS = re.compile(r"^\s*segments (\d+)/(\d+) \(\d+ cached\)$")
_FILES_PROGRESS = re.compile(r"^\s*files \d+/\d+ \(([\d.]+)/([\d.]+) GiB\)$")
_FILES_TOTAL = re.compile(r"^Assembling and validating \d+ files")
_MIB_PER_GIB = 1024
_SEGMENTS_TOTAL = re.compile(r"^Preparing (\d+) unique segments")


def parse_download_progress(line: str) -> tuple[str, int, int] | None:
    """Parse one line of the downloader's progress output, if it is one.

    Download progress counts segments; assembly progress counts MiB.
    """
    if match := _SEGMENTS_TOTAL.match(line):
        return "Preparing segments", 0, int(match.group(1))
    if match := _SEGMENTS_PROGRESS.match(line):
        return "Downloading", int(match.group(1)), int(match.group(2))
    if _FILES_TOTAL.match(line):
        return "Assembling files", 0, 0
    if match := _FILES_PROGRESS.match(line):
        # By size, not file count: one game archive can be most of the build.
        return (
            "Assembling files",
            round(float(match.group(1)) * _MIB_PER_GIB),
            round(float(match.group(2)) * _MIB_PER_GIB),
        )
    return None


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


def _download_path(path: Path) -> Path:
    """Allow downloader temporary filenames beyond Windows' legacy path limit."""
    if os.name != "nt":
        return path
    absolute = str(path.expanduser().resolve())
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def split_launch_arguments(value: str) -> list[str]:
    """Split a Windows launch string without retaining quotes or eating slashes."""
    lexer = shlex.shlex(value, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    return list(lexer)


def join_launch_arguments(arguments: list[str]) -> str:
    """Format arguments so split_launch_arguments returns them unchanged."""

    def quote(argument: str) -> str:
        if argument and not any(c.isspace() or c in "\"'" for c in argument):
            return argument
        # Quotes cannot be escaped, so close the double-quoted run around each
        # literal double quote and supply it single-quoted instead.
        return "'\"'".join(f'"{part}"' for part in argument.split('"'))

    return " ".join(quote(argument) for argument in arguments)


def _best_executable(directory: Path, manifest: dict, override: str | None) -> str:
    preferred = _path(override or str(manifest.get("launchFile") or ""))
    if override and not preferred.name:
        raise ValueError("--executable cannot be empty")
    if not preferred.name and override is None:
        raise ValueError("Meta manifest has no launch executable; pass --executable")
    directory = directory.resolve()
    try:
        (directory / preferred).resolve().relative_to(directory)
    except ValueError as error:
        source = "--executable" if override is not None else "Meta manifest"
        raise ValueError(
            f"{source} launch path must stay inside the game folder"
        ) from error
    candidate = best_windows_executable(directory, preferred)
    return candidate.relative_to(directory).as_posix()


def _launch_arguments(
    directory: Path, executable: str, manifest: dict, override: str | None
) -> list[str]:
    value = (
        override
        if override is not None
        else str(manifest.get("launchParameters") or "")
    )
    arguments = split_launch_arguments(value)
    if override is None and is_unreal_shipping(directory / executable):
        vr_options = {"-vr", "-oculus", "-openxr", "-steamvr"}
        if not any(argument.casefold() in vr_options for argument in arguments):
            arguments.append("-vr")
    return arguments


def _owned_build(
    paths: Paths, app_id: str, build_selector: str | None
) -> tuple[str, object, dict]:
    """Find a signed-in account that may download the app and its manifest."""
    failure: Exception | None = None
    for token in account_tokens(paths, app_id):
        try:
            build = select_build(list_builds(token, app_id), build_selector)
            return token, build, fetch_manifest(token, build)
        except (DownloadError, MetaApiError) as error:
            # Another signed-in account may own it; keep the first reason.
            failure = failure or error
    assert failure is not None
    raise failure


def add(
    paths: Paths,
    app: str,
    *,
    build_selector: str | None = None,
    executable: str | None = None,
    arguments: str | None = None,
    jobs: int | None = None,
    on_finalizing: Callable[[], None] | None = None,
) -> Game:
    paths.create()
    app_id = parse_app_id(app)
    print("Reading your persistent RiftLift Meta login...")
    token, build, manifest = _owned_build(paths, app_id, build_selector)
    slug = slugify(build.app_name)
    directory = paths.games / slug
    print(f"Downloading {build.app_name} {build.version}...")
    workers = default_download_workers() if jobs is None else jobs
    print(f"Using {workers} download workers.")
    Downloader(
        token,
        build,
        _download_path(directory),
        _download_path(paths.cache / "segments"),
        workers,
    ).run(manifest)
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
        platform_shim=True,
        source="meta",
    )
    if on_finalizing is not None:
        on_finalizing()
    game.save(paths)
    try:
        populate_game_metadata(paths, game)
    except RiftLiftError as error:
        print(f"warning: catalog metadata was not available: {error}")
    return game


def remove(paths: Paths, game: Game) -> None:
    """Remove a game from RiftLift, deleting its downloaded files if RiftLift owns them."""
    if game.source == "meta":
        # A failure here must keep the record, so the removal can be retried.
        with contextlib.suppress(FileNotFoundError):
            shutil.rmtree(game.game_dir)
    shutil.rmtree(paths.data / "artwork" / game.slug, ignore_errors=True)
    game.delete(paths)


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
    launch_arguments = split_launch_arguments(arguments) if arguments else []
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
