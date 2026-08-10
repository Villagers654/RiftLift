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
from .library import add
from .metadata import populate_game_metadata
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
        "--login", action="store_true", help="open Meta Horizon Link after setup"
    )
    commands.add_parser(
        "login", help="open Meta Horizon Link in the persistent shared prefix"
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
    commands.add_parser("doctor", help="verify the runtime, Steam, and installed games")
    return root


def run(arguments: argparse.Namespace) -> int:
    paths = Paths.defaults()
    if arguments.command == "gui":
        from .gui import main as gui_main

        return gui_main()
    if arguments.command == "setup":
        setup(paths)
        print("RiftLift compatibility stack is ready.")
        return login(paths) if arguments.login else 0
    if arguments.command == "login":
        return login(paths)
    if arguments.command == "callback":
        import os

        url = arguments.url or os.environ.get("RIFTLIFT_CALLBACK_URL", "")
        return complete_login(paths, url)
    if arguments.command == "add":
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
            target = sync_with_restart(paths)
            print(f"Added to Steam ({target}).")
        return 0
    if arguments.command == "launch":
        return launch(paths, Game.load(paths, arguments.slug), arguments.arguments)
    if arguments.command == "launch-steam":
        # Steam expands %command% after `--`. The depot must instead run inside
        # RiftLift's shared prefix so Revive can inject before Oculus starts.
        import subprocess

        from .steam_oculus import game_from_steam_command, steam_command_uses_oculus

        discovered = steam_oculus_game(arguments.app_id)
        if not steam_command_uses_oculus(discovered, arguments.steam_command):
            # The title also supports OpenVR and Steam selected that mode. Keep
            # the host's existing XRizer path instead of forcing Revive onto a
            # non-Oculus launch choice.
            command = (
                arguments.steam_command[1:]
                if arguments.steam_command[:1] == ["--"]
                else arguments.steam_command
            )
            if not command:
                raise RiftLiftError("Steam did not provide an expanded launch command")
            return subprocess.call(command, cwd=discovered.game_dir)
        game = game_from_steam_command(discovered, arguments.steam_command)
        return launch(paths, game, [])
    if arguments.command == "steam-oculus-ids":
        for game in steam_oculus_games():
            print(game.app_id)
        return 0
    if arguments.command == "list":
        installed = games(paths)
        if not installed:
            print("No games installed. Use: riftlift add META_RIFT_STORE_URL")
        for game in installed:
            print(f"{game.slug:<36} {game.name} {game.version}")
        return 0
    if arguments.command == "steam-sync":
        print(sync_with_restart(paths))
        return 0
    if arguments.command == "metadata":
        installed = (
            [Game.load(paths, arguments.slug)] if arguments.slug else games(paths)
        )
        for game in installed:
            populate_game_metadata(paths, game, refresh=arguments.refresh)
            print(f"Updated metadata for {game.name}.")
        return 0
    if arguments.command == "doctor":
        return doctor(paths)
    raise AssertionError(arguments.command)


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parser().parse_args(argv))
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
