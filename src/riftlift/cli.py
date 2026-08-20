from __future__ import annotations

import argparse
import sys

from meta_pcvr_downloader.api import MetaApiError
from meta_pcvr_downloader.auth import AuthenticationError
from meta_pcvr_downloader.download import DownloadError

from . import __version__
from .config import Game, Paths, games
from .doctor import doctor
from .launch import launch
from .library import add, add_local
from .metadata import populate_game_metadata
from .playtime import playtime, playtime_label
from .runtime import complete_login, login, setup
from .steam import sync_with_restart
from .steam_oculus import steam_oculus_game, steam_oculus_games
from .util import RiftLiftError


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="riftlift",
        description="Run owned Meta Rift games on Linux OpenXR/Monado.",
    )
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = root.add_subparsers(dest="command", required=True)

    commands.add_parser("gui", help="open the RiftLift desktop app")

    setup_command = commands.add_parser(
        "setup", help="install/update the shared compatibility stack"
    )
    setup_command.add_argument(
        "--login", action="store_true", help="start browser-backed Meta sign-in"
    )
    commands.add_parser(
        "login", help="sign in to Meta through an isolated default-browser window"
    )
    callback = commands.add_parser("callback", help=argparse.SUPPRESS)
    callback.add_argument("url", nargs="?", help=argparse.SUPPRESS)

    add_command = commands.add_parser(
        "add", help="download an owned Rift game and add it to Steam"
    )
    add_command.add_argument("app", help="Meta Rift store URL or numeric app ID")
    add_command.add_argument(
        "--build", help="specific version, version code, or binary ID"
    )
    add_command.add_argument(
        "--executable", help="override the manifest launch executable"
    )
    add_command.add_argument(
        "--arguments", help="override the manifest launch arguments"
    )
    add_command.add_argument(
        "--jobs",
        type=int,
        choices=range(1, 33),
        metavar="1-32",
        help="download workers (default: adapts to available CPUs)",
    )
    add_command.add_argument(
        "--no-steam", action="store_true", help="download without updating Steam"
    )

    local_command = commands.add_parser(
        "add-local", help="add an existing Windows VR game to RiftLift"
    )
    local_command.add_argument("executable", help="path to the game's .exe file")
    local_command.add_argument("--name", help="library name (default: executable name)")
    local_command.add_argument(
        "--root", help="game folder containing the executable (default: its folder)"
    )
    local_command.add_argument("--arguments", help="launch arguments")
    local_command.add_argument(
        "--app-key", help="Oculus application key (advanced; normally unnecessary)"
    )
    local_command.add_argument("--artwork", help="cover image file")
    local_command.add_argument("--game-version", default="", help="displayed version")
    local_command.add_argument(
        "--no-steam", action="store_true", help="register without updating Steam"
    )

    launch_command = commands.add_parser("launch", help="launch an installed game")
    launch_command.add_argument("slug")
    launch_command.add_argument("arguments", nargs=argparse.REMAINDER)
    steam_launch = commands.add_parser(
        "launch-steam", help="launch an installed Steam Oculus XR game"
    )
    steam_launch.add_argument("app_id")
    steam_launch.add_argument("steam_command", nargs=argparse.REMAINDER)
    commands.add_parser(
        "steam-oculus-ids", help="list installed Steam games needing RiftLift"
    )
    commands.add_parser("list", help="list installed RiftLift games")
    commands.add_parser(
        "steam-sync", help="safely synchronize all RiftLift games into Steam"
    )
    metadata_command = commands.add_parser(
        "metadata", help="fetch artwork and catalog metadata"
    )
    metadata_command.add_argument(
        "slug", nargs="?", help="one installed game (default: all)"
    )
    metadata_command.add_argument(
        "--refresh", action="store_true", help="refresh cached catalog data and artwork"
    )
    doctor_command = commands.add_parser(
        "doctor", help="create a shareable runtime and recent-launch diagnostic report"
    )
    doctor_command.add_argument(
        "--no-paste",
        action="store_true",
        help="print locally without creating a public paste",
    )
    return root


def _run_gui(_paths: Paths, _arguments: argparse.Namespace) -> int:
    from .gui import main as gui_main

    return gui_main()


def _run_setup(paths: Paths, arguments: argparse.Namespace) -> int:
    setup(paths)
    print("RiftLift compatibility stack is ready.")
    return login(paths) if arguments.login else 0


def _run_login(paths: Paths, _arguments: argparse.Namespace) -> int:
    return login(paths)


def _run_callback(paths: Paths, arguments: argparse.Namespace) -> int:
    return complete_login(paths, arguments.url or "")


def _run_add(paths: Paths, arguments: argparse.Namespace) -> int:
    game = add(
        paths,
        arguments.app,
        build_selector=arguments.build,
        executable=arguments.executable,
        arguments=arguments.arguments,
        jobs=arguments.jobs,
    )
    print(f"Installed {game.name} as {game.slug}.")
    if not arguments.no_steam:
        print(f"Added to Steam ({sync_with_restart(paths)}).")
    return 0


def _run_add_local(paths: Paths, arguments: argparse.Namespace) -> int:
    game = add_local(
        paths,
        arguments.executable,
        name=arguments.name,
        root=arguments.root,
        arguments=arguments.arguments,
        app_key=arguments.app_key,
        artwork=arguments.artwork,
        version=arguments.game_version,
    )
    print(f"Added local game {game.name} as {game.slug}.")
    if not arguments.no_steam:
        print(f"Added to Steam ({sync_with_restart(paths)}).")
    return 0


def _run_steam_launch(paths: Paths, arguments: argparse.Namespace) -> int:
    from .steam_oculus import game_from_steam_command

    if arguments.steam_command in (["-h"], ["--help"]):
        parser().parse_args(["launch-steam", "--help"])
    discovered = steam_oculus_game(arguments.app_id)
    return launch(
        paths, game_from_steam_command(discovered, arguments.steam_command), []
    )


def _run_launch(paths: Paths, arguments: argparse.Namespace) -> int:
    if arguments.arguments in (["-h"], ["--help"]):
        parser().parse_args(["launch", "--help"])
    return launch(paths, Game.load(paths, arguments.slug), arguments.arguments)


def _run_steam_oculus_ids(_paths: Paths, _arguments: argparse.Namespace) -> int:
    for game in steam_oculus_games():
        print(game.app_id)
    return 0


def _run_list(paths: Paths, _arguments: argparse.Namespace) -> int:
    installed = games(paths)
    if not installed:
        print(
            "No games installed. Use 'riftlift add STORE_URL' or "
            "'riftlift add-local GAME.exe'."
        )
    for game in installed:
        played = playtime_label(playtime(paths, game.slug))
        print(f"{game.slug:<36} {game.name} {game.version} [{played}]")
    return 0


def _run_metadata(paths: Paths, arguments: argparse.Namespace) -> int:
    installed = [Game.load(paths, arguments.slug)] if arguments.slug else games(paths)
    for game in installed:
        populate_game_metadata(paths, game, refresh=arguments.refresh)
        print(f"Updated metadata for {game.name}.")
    return 0


def _run_steam_sync(paths: Paths, _arguments: argparse.Namespace) -> int:
    print(sync_with_restart(paths))
    return 0


def _run_doctor(paths: Paths, arguments: argparse.Namespace) -> int:
    return doctor(paths, paste=not arguments.no_paste)


def run(arguments: argparse.Namespace) -> int:
    handlers = {
        "gui": _run_gui,
        "setup": _run_setup,
        "login": _run_login,
        "callback": _run_callback,
        "add": _run_add,
        "add-local": _run_add_local,
        "launch": _run_launch,
        "launch-steam": _run_steam_launch,
        "steam-oculus-ids": _run_steam_oculus_ids,
        "list": _run_list,
        "steam-sync": _run_steam_sync,
        "metadata": _run_metadata,
        "doctor": _run_doctor,
    }
    return handlers[arguments.command](Paths.defaults(), arguments)


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    try:
        return run(parser().parse_args(values))
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except (
        AuthenticationError,
        DownloadError,
        MetaApiError,
        RiftLiftError,
        ValueError,
        OSError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
