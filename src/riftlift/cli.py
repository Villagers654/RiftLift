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
from .runtime import complete_login, login, setup
from .steam import sync_with_restart
from .util import RiftLiftError


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="riftlift",
        description="Run owned Meta Rift games on Linux OpenXR/Monado.",
    )
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = root.add_subparsers(dest="command", required=True)

    setup_command = commands.add_parser("setup", help="install/update the shared compatibility stack")
    setup_command.add_argument("--login", action="store_true", help="open Meta Horizon Link after setup")
    commands.add_parser("login", help="open Meta Horizon Link in the persistent shared prefix")
    callback = commands.add_parser("callback", help=argparse.SUPPRESS)
    callback.add_argument("url", nargs="?", help=argparse.SUPPRESS)

    add_command = commands.add_parser("add", help="download an owned Rift game and add it to Steam")
    add_command.add_argument("app", help="Meta Rift store URL or numeric app ID")
    add_command.add_argument("--build", help="specific version, version code, or binary ID")
    add_command.add_argument("--executable", help="override the manifest launch executable")
    add_command.add_argument("--arguments", help="override the manifest launch arguments")
    add_command.add_argument("--jobs", type=int, default=8, choices=range(1, 33), metavar="1-32")
    add_command.add_argument("--no-steam", action="store_true", help="download without updating Steam")

    launch_command = commands.add_parser("launch", help="launch an installed game")
    launch_command.add_argument("slug")
    launch_command.add_argument("arguments", nargs=argparse.REMAINDER)
    commands.add_parser("list", help="list installed RiftLift games")
    commands.add_parser("steam-sync", help="safely synchronize all RiftLift games into Steam")
    commands.add_parser("doctor", help="verify the runtime, Steam, and installed games")
    return root


def run(arguments: argparse.Namespace) -> int:
    paths = Paths.defaults()
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
    if arguments.command == "doctor":
        return doctor(paths)
    raise AssertionError(arguments.command)


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parser().parse_args(argv))
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except (AuthenticationError, DownloadError, MetaApiError, RiftLiftError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
