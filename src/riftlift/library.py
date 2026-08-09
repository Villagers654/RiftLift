from __future__ import annotations

import re
import shlex
from pathlib import Path

from meta_pcvr_downloader.api import list_builds, parse_app_id, select_build
from meta_pcvr_downloader.auth import get_access_token
from meta_pcvr_downloader.download import Downloader, fetch_manifest

from .config import Game, Paths


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "meta-rift-game"


def _path(value: str) -> Path:
    return Path(value.replace("\\", "/"))


def _best_executable(directory: Path, manifest: dict, override: str | None) -> str:
    if override:
        candidate = _path(override)
    else:
        candidate = _path(str(manifest.get("launchFile") or ""))
    if not candidate.name:
        raise ValueError("Meta manifest has no launch executable; pass --executable")
    if not (directory / candidate).is_file():
        raise ValueError(f"launch executable is missing after download: {candidate}")
    return candidate.as_posix()


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
    print("Reading the Meta login from your browser (the token is never saved)...")
    token = get_access_token()
    build = select_build(list_builds(token, app_id), build_selector)
    slug = slugify(build.app_name)
    directory = paths.games / slug
    print(f"Downloading {build.app_name} {build.version}...")
    manifest = fetch_manifest(token, build)
    Downloader(token, build, directory, paths.cache / "segments", jobs).run(manifest)
    launch_file = _best_executable(directory, manifest, executable)
    launch_arguments = shlex.split(arguments) if arguments is not None else []
    if arguments is None and manifest.get("launchParameters"):
        launch_arguments = shlex.split(str(manifest["launchParameters"]), posix=False)
    game = Game(
        slug=slug,
        name=build.app_name,
        app_id=app_id,
        app_key=str(manifest.get("canonicalName") or slug),
        directory=str(directory.resolve()),
        executable=launch_file,
        arguments=launch_arguments,
        version=build.version,
        platform_offline="vader-immortal" in str(manifest.get("canonicalName", "")),
    )
    game.save(paths)
    return game
