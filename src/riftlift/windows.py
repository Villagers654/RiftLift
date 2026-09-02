"""Native Windows backend for RiftLift's shared CLI and desktop application."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from . import __version__
from .config import Game, Paths, games
from .detection import is_pe64
from .util import RiftLiftError

RELEASE = "v0.10.2.2"
PAYLOAD_SHA256 = "90f9b1b5b26ba85a25ad2dcb3707b7a17540b0d40d310148dd98fa76c3a619eb"
PAYLOAD_URL = f"https://github.com/Villagers654/RiftLift/releases/download/{RELEASE}/riftlift-compat.zip"
FILES = {
    "RiftLiftLauncher.exe",
    "RiftLiftOpenXR64.dll",
    "RiftLiftOpenVR64.dll",
    "openvr_api64.dll",
    "LICENSE",
    "RIFTLIFT-LICENSE",
}


def runtime_dir(paths: Paths) -> Path:
    return paths.tools / "windows-native" / RELEASE


def install_payload(paths: Paths, archive: Path | None = None) -> Path:
    if archive:
        payload = archive.read_bytes()
    else:
        with urllib.request.urlopen(PAYLOAD_URL, timeout=60) as response:
            payload = response.read(32 * 1024 * 1024 + 1)
    if hashlib.sha256(payload).hexdigest() != PAYLOAD_SHA256:
        raise RiftLiftError("Native payload SHA256 mismatch")
    target = runtime_dir(paths)
    # Validate the complete archive before writing; never install the Linux
    # platform-entitlement shim into a native Windows game.
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        names = {i.filename.replace("\\", "/") for i in bundle.infolist()}
        if not FILES.issubset(names):
            raise RiftLiftError("Native payload is missing required files")
        selected = []
        for entry in bundle.infolist():
            name = entry.filename.replace("\\", "/")
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or ":" in name:
                raise RiftLiftError("Invalid native payload path")
            if name in FILES or (name.startswith("Input/") and name.endswith(".json")):
                selected.append((relative, bundle.read(entry)))
        for relative, data in selected:
            dest = target.joinpath(*relative.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    return target


def active_openxr() -> Path | None:
    if override := os.environ.get("XR_RUNTIME_JSON"):
        return Path(override)
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Khronos\OpenXR\1",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            return Path(winreg.QueryValueEx(key, "ActiveRuntime")[0])
    except OSError:
        return None


def active_openvr() -> Path | None:
    config = Path(os.environ["LOCALAPPDATA"]) / "openvr/openvrpaths.vrpath"
    try:
        roots = json.loads(config.read_text(encoding="utf-8"))["runtime"]
        for root in roots:
            candidate = Path(root)
            if (candidate / "bin/win64/vrclient_x64.dll").is_file():
                return candidate
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def runtime_ready(backend: str) -> bool:
    target = active_openxr() if backend == "openxr" else active_openvr()
    return bool(
        target and (target.is_file() if backend == "openxr" else target.is_dir())
    )


def doctor(paths: Paths) -> tuple[str, int]:
    native = runtime_dir(paths)
    payload_ok = all((native / name).is_file() for name in FILES)
    xr, vr = active_openxr(), active_openvr()
    installed = games(paths)
    text = "\n".join(
        [
            f"RiftLift {__version__} on Windows",
            f"Native payload: {'INSTALLED' if payload_ok else 'MISSING'} ({native})",
            f"OpenXR manifest: {xr or 'NOT REGISTERED'}",
            f"OpenVR runtime: {vr or 'NOT REGISTERED'}",
            f"Registered games: {len(installed)}",
            "Game entitlement: handled by each game's original Meta platform runtime",
            "Headset rendering/input/audio: NOT VERIFIED",
            "Diagnostics remain local; no paste is uploaded.",
        ]
    )
    return text, 0 if payload_ok and (
        runtime_ready("openxr") or runtime_ready("openvr")
    ) else 2


def add_local(
    paths: Paths,
    executable: str,
    name: str | None = None,
    root: str | None = None,
    arguments: str | None = None,
    artwork: str | None = None,
) -> Game:
    from .library import add_local as register

    if not is_pe64(Path(executable)):
        raise RiftLiftError("Native Windows launcher requires an x64 PE executable")
    game = register(
        paths, executable, name=name, root=root, arguments=arguments, artwork=artwork
    )
    game.platform_shim = False
    game.platform_offline = False
    game.save(paths)
    return game


def launch_command(
    paths: Paths, game: Game, backend: str, extra: list[str] | None = None
) -> list[str]:
    if backend not in {"openxr", "openvr"}:
        raise RiftLiftError("Unknown native backend")
    native = runtime_dir(paths)
    required = (
        ["RiftLiftLauncher.exe", "RiftLiftOpenXR64.dll"]
        if backend == "openxr"
        else ["RiftLiftLauncher.exe", "RiftLiftOpenVR64.dll", "openvr_api64.dll"]
    )
    if not all((native / name).is_file() for name in required):
        raise RiftLiftError("Run RiftLift setup to install the native payload")
    executable = game.executable_path.resolve()
    if not executable.is_relative_to(game.game_dir.resolve()) or not is_pe64(
        executable
    ):
        raise RiftLiftError(
            "Game executable must be an existing x64 PE inside its game folder"
        )
    return [
        str(native / "RiftLiftLauncher.exe"),
        f"/{backend}",
        "/wait",
        "/cwd",
        str(game.game_dir),
        str(executable),
        *game.arguments,
        *(extra or []),
    ]


def launch(
    paths: Paths,
    game: Game,
    backend: str,
    dry_run: bool = False,
    extra: list[str] | None = None,
) -> int:
    command = launch_command(paths, game, backend, extra)
    if dry_run:
        print(subprocess.list2cmdline(command))
        return 0
    if not runtime_ready(backend):
        raise RiftLiftError(
            f"Configure a Windows {backend} runtime and connect the headset first"
        )
    # Do not write to game files or install the Linux platform shim.
    log = paths.data / "logs" / f"{game.slug}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    from .playtime import PlaytimeSession

    with log.open("a", encoding="utf-8") as stream, PlaytimeSession(paths, game.slug):
        process = subprocess.run(
            command,
            cwd=game.game_dir,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    print(f"Launcher exit: {process.returncode}; log: {log}")
    return process.returncode


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="riftlift",
        description="Run Meta Rift games through native Windows VR runtimes.",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("gui", help="open the Windows library")
    setup = sub.add_parser("setup", help="install checksum-verified native x64 runtime")
    setup.add_argument("--archive", type=Path)
    doc = sub.add_parser("doctor", help="local Windows runtime checks")
    doc.add_argument("--no-paste", action="store_true")
    sub.add_parser("list")
    add = sub.add_parser(
        "add-local", help="reference an installed game without changing its files"
    )
    add.add_argument("executable")
    add.add_argument("--name")
    add.add_argument("--root")
    add.add_argument("--arguments")
    launch_parser = sub.add_parser("launch")
    launch_parser.add_argument("slug")
    launch_parser.add_argument(
        "--backend", choices=["openxr", "openvr"], default="openxr"
    )
    launch_parser.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    paths = Paths.defaults()
    try:
        if args.command == "gui":
            from .gui import main as gui

            return gui()
        if args.command == "setup":
            print(f"Native payload installed: {install_payload(paths, args.archive)}")
        elif args.command == "doctor":
            report, status = doctor(paths)
            print(report)
            return status
        elif args.command == "list":
            installed = games(paths)
            print(
                "\n".join(f"{g.slug}: {g.name}" for g in installed)
                or "No games registered."
            )
        elif args.command == "add-local":
            game = add_local(
                paths, args.executable, args.name, args.root, args.arguments
            )
            print(f"Registered: {game.slug}")
        elif args.command == "launch":
            return launch(
                paths, Game.load(paths, args.slug), args.backend, args.dry_run
            )
        return 0
    except (OSError, ValueError, RiftLiftError, zipfile.BadZipFile) as error:
        print(f"RiftLift: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
