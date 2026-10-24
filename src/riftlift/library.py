from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
from collections.abc import Callable
from pathlib import Path

from meta_pcvr_downloader.api import (
    Build,
    MetaApiError,
    parse_app_id,
    select_build,
)
from meta_pcvr_downloader.download import Downloader, DownloadError, fetch_manifest

from .auth import account_tokens
from .builds import AvailableBuild, build_label, default_build, list_all_builds
from .config import Game, Paths, games
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


ALL_BUILDS = "all"


class NotLaunchableError(ValueError):
    """A build downloaded completely, but RiftLift cannot launch it."""

    def __init__(self, directory: Path, reason: Exception) -> None:
        super().__init__(
            f"downloaded to {directory}, but RiftLift cannot launch it: {reason}"
        )
        self.directory = directory


def _account_builds(paths: Paths, app_id: str) -> tuple[str, list[AvailableBuild]]:
    """Find a signed-in account that may download the app, and list its builds."""
    failure: Exception | None = None
    for token in account_tokens(paths, app_id):
        try:
            return token, list_all_builds(token, app_id)
        except (DownloadError, MetaApiError) as error:
            # Another signed-in account may own it; keep the first reason.
            failure = failure or error
    assert failure is not None
    raise failure


def available_builds(paths: Paths, app: str) -> list[AvailableBuild]:
    """Return every build of ``app`` a signed-in account can download."""
    return _account_builds(paths, parse_app_id(app))[1]


def _owned_build(
    paths: Paths,
    app_id: str,
    build_selector: str | None,
    builds: list[Build] | None = None,
) -> tuple[str, Build, dict]:
    """Find a signed-in account that may download the build, and its manifest.

    ``builds`` is an earlier listing to choose from instead of asking Meta again.
    """
    failure: Exception | None = None
    for token in account_tokens(paths, app_id):
        try:
            listed = list_all_builds(token, app_id) if builds is None else builds
            build = (
                default_build(listed)
                if build_selector is None
                else select_build(listed, build_selector)
            )
            return token, build, fetch_manifest(token, build)
        except (DownloadError, MetaApiError) as error:
            # Another signed-in account may own it; keep the first reason.
            failure = failure or error
    assert failure is not None
    raise failure


def _existing_meta_game(paths: Paths, slug: str) -> Game | None:
    if not (paths.data / "games" / f"{slug}.json").exists():
        return None
    try:
        game = Game.load(paths, slug)
    except ValueError:
        return None
    return game if game.source == "meta" else None


BUILD_MARKER = ".riftlift-build"


def _same_build(game: Game, build: Build) -> bool:
    # Records written before binary IDs were stored only know the version.
    if game.binary_id:
        return game.binary_id == build.binary_id
    return game.version == build.version


def _folder_build(paths: Paths, slug: str) -> str | None:
    """Return the binary ID a game folder holds or is downloading, if known."""
    try:
        return (paths.games / slug / BUILD_MARKER).read_text().strip() or None
    except (FileNotFoundError, OSError, UnicodeError):
        return None


def _slot_free_for(paths: Paths, slug: str, build: Build) -> bool:
    """Whether ``build`` may download into ``paths.games / slug``."""
    record = _existing_meta_game(paths, slug)
    if record is not None:
        return _same_build(record, build)
    if not (paths.games / slug).exists():
        return True
    # A folder without a library record, e.g. a build RiftLift cannot launch.
    return _folder_build(paths, slug) == build.binary_id


def _install_identity(
    paths: Paths, build: Build, *, side_by_side: bool, force: bool = False
) -> tuple[str, str]:
    """Return the slug and display name a build installs under.

    A plain install keeps one folder per game, so installing again updates it.
    An explicitly chosen build that differs from the installed one gets its
    own folder and library entry, so several versions can coexist. Builds that
    share a version string are told apart by their version code, and by their
    binary ID if that name is taken too.
    """
    slug = slugify(build.app_name)
    existing = _existing_meta_game(paths, slug)
    if existing is not None and _same_build(existing, build):
        return slug, build.app_name
    if not (force or side_by_side):
        return slug, build.app_name
    # The first chosen version keeps the plain folder, unless another build
    # is paused there.
    if existing is None and not force and _slot_free_for(paths, slug, build):
        return slug, build.app_name
    versioned = f"{slug}-{slugify(build.version)}"
    if _slot_free_for(paths, versioned, build):
        return versioned, f"{build.app_name} ({build.version})"
    name = f"{build.app_name} ({build.version}, build {build.version_code})"
    by_code = f"{versioned}-{build.version_code}"
    if _slot_free_for(paths, by_code, build):
        return by_code, name
    return f"{versioned}-{slugify(build.binary_id)}", name


def add(
    paths: Paths,
    app: str,
    *,
    build_selector: str | None = None,
    executable: str | None = None,
    arguments: str | None = None,
    jobs: int | None = None,
    on_finalizing: Callable[[], None] | None = None,
    builds: list[Build] | None = None,
    separate_version: bool = False,
    catalog: Game | None = None,
) -> Game:
    """Download one build and add it to the library.

    ``builds`` lets callers that already listed the builds skip a second
    request. ``separate_version`` forces a versioned folder even when no other
    version is installed yet. ``catalog`` is another version of the same game
    whose store details and artwork are copied instead of fetched again.
    """
    if build_selector == ALL_BUILDS:
        raise ValueError("use add_all_versions to download every build")
    paths.create()
    app_id = parse_app_id(app)
    print("Reading your persistent RiftLift Meta login...")
    token, build, manifest = _owned_build(paths, app_id, build_selector, builds)
    slug, name = _install_identity(
        paths,
        build,
        side_by_side=build_selector is not None,
        force=separate_version,
    )
    directory = paths.games / slug
    # Claim the folder before downloading: a paused download then resumes
    # into it instead of leaving it behind for a new folder.
    directory.mkdir(parents=True, exist_ok=True)
    (directory / BUILD_MARKER).write_text(build.binary_id + "\n")
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
    try:
        launch_file = _best_executable(directory, manifest, executable)
    except ValueError as error:
        if executable is not None:
            raise
        raise NotLaunchableError(directory, error) from error
    launch_arguments = _launch_arguments(directory, launch_file, manifest, arguments)
    game = Game(
        slug=slug,
        name=name,
        app_id=app_id,
        app_key=str(manifest.get("canonicalName") or slugify(build.app_name)),
        directory=str(directory.resolve()),
        executable=launch_file,
        arguments=launch_arguments,
        version=build.version,
        platform_offline=True,
        platform_shim=True,
        source="meta",
        binary_id=build.binary_id,
    )
    if on_finalizing is not None:
        on_finalizing()
    game.save(paths)
    if catalog is not None:
        _copy_catalog(paths, catalog, game)
        return game
    try:
        populate_game_metadata(paths, game)
    except RiftLiftError as error:
        print(f"warning: catalog metadata was not available: {error}")
    return game


def _copy_catalog(paths: Paths, source: Game, game: Game) -> None:
    """Give ``game`` the store details and artwork of another version of it."""
    game.store_url = source.store_url
    game.description = source.description
    game.description_lang = source.description_lang
    game.developer = source.developer
    game.publisher = source.publisher
    game.genres = list(source.genres)
    artwork = paths.data / "artwork"
    if source.artwork and (artwork / source.slug).is_dir():
        shutil.copytree(artwork / source.slug, artwork / game.slug, dirs_exist_ok=True)
        game.artwork = {
            kind: str(artwork / game.slug / Path(path).name)
            for kind, path in source.artwork.items()
        }
    game.save(paths)


def _installed_record(paths: Paths, app_id: str, build: Build) -> Game | None:
    """Return the library record that already holds ``build``, if any."""
    for game in games(paths):
        if (
            game.source == "meta"
            and game.app_id == app_id
            and _same_build(game, build)
            and game.game_dir.is_dir()
        ):
            return game
    return None


def add_all_versions(
    paths: Paths,
    app: str,
    *,
    jobs: int | None = None,
    builds: list[Build] | None = None,
    on_build: Callable[[int, int, Build], None] | None = None,
    on_finalizing: Callable[[], None] | None = None,
) -> tuple[list[Game], list[tuple[Build, Exception]]]:
    """Download every available build, each into its own versioned folder.

    A failed build does not stop the others. Returns the installed games and
    the builds that failed with their errors.
    """
    if builds is None:
        builds = available_builds(paths, app)
    installed: list[Game] = []
    failed: list[tuple[Build, Exception]] = []
    # Every version shares the store page: fetch it once, not once per version,
    # which also keeps Meta from rate-limiting the rest of the run.
    catalog: Game | None = None
    app_id = parse_app_id(app)
    for index, build in enumerate(builds, start=1):
        print(f"Version {index}/{len(builds)}: {build_label(build)}")
        if on_build is not None:
            on_build(index, len(builds), build)
        # An installed version keeps its record and launch options; resuming
        # a paused run also skips the versions it already finished.
        present = _installed_record(paths, app_id, build)
        if present is not None:
            installed.append(present)
            if catalog is None and present.description:
                catalog = present
            continue
        try:
            game = add(
                paths,
                app,
                build_selector=build.binary_id,
                jobs=jobs,
                builds=builds,
                separate_version=True,
                on_finalizing=on_finalizing,
                catalog=catalog,
            )
            installed.append(game)
            if catalog is None and game.description:
                catalog = game
        except Exception as error:  # keep downloading the remaining builds
            print(f"warning: {build_label(build)} failed: {error}")
            failed.append((build, error))
    return installed, failed


def all_versions_summary(
    installed: list[Game], failed: list[tuple[Build, Exception]]
) -> list[str]:
    """Lines summing up add_all_versions for the command line."""
    unlaunchable = [item for item in failed if isinstance(item[1], NotLaunchableError)]
    return [
        f"Installed {len(installed)} version(s); "
        f"{len(unlaunchable)} downloaded but not launchable; "
        f"{len(failed) - len(unlaunchable)} failed.",
        *(f"  {build_label(build)}: {error}" for build, error in failed),
    ]


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
