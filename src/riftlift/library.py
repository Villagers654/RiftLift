from __future__ import annotations

import re
import shlex
from pathlib import Path

from meta_pcvr_downloader.api import list_builds, parse_app_id, select_build
from meta_pcvr_downloader.download import Downloader, fetch_manifest

from .auth import runtime_access_token
from .config import Game, Paths
from .detection import best_windows_executable, is_unreal_shipping
from .metadata import populate_game_metadata
from .util import RiftLiftError


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "meta-rift-game"


def _path(value: str) -> Path:
    return Path(value.replace("\\", "/"))


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
    arguments = (
        shlex.split(override)
        if override is not None
        else shlex.split(str(manifest.get("launchParameters") or ""), posix=False)
    )
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
    jobs: int = 8,
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
    Downloader(token, build, directory, paths.cache / "segments", jobs).run(manifest)
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
        # Every Rift Store download is entitlement-checked above. Keep that
        # verified identity available to legacy Platform SDK titles without a
        # per-game allowlist.
        platform_offline=True,
    )
    game.save(paths)
    try:
        populate_game_metadata(paths, game)
    except RiftLiftError as error:
        print(f"warning: catalog metadata was not available: {error}")
    return game
