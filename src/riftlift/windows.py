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
from datetime import datetime
from pathlib import Path, PurePosixPath

from meta_pcvr_downloader.api import MetaApiError
from meta_pcvr_downloader.download import DownloadError

from . import __version__
from .config import Game, Paths, debug_logging_enabled, games
from .detection import is_pe64
from .util import RiftLiftError, atomic_write_bytes, atomic_write_text, download, sha256
from .xr_runtime import platform_user_id

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
PLATFORM_FILES = {
    "LibOVRPlatform64_1.dll",
    "LibOVRPlatformImpl64_1.dll",
    "LibOVRPlatformImpl64_1_real.dll",
    "LibOVRP2P64_1.dll",
    "LibOVRRT64_1.dll",
}
SDK_RUNTIME_SHA256 = "f6941275692026b18666bb856d71fe1b19462017b2b2e556fe8df82461f493f5"
# OVRPlugin (Unity/Unreal Oculus integrations) talks OpenXR directly and refuses
# runtimes that do not identify as Oculus. Packaged builds always carry this
# layer; the pinned source payload predates it, so launches only add it when present.
OPENXR_LAYER_FILE = "RiftLiftOpenXRLayer.dll"
OPENXR_LAYER_NAME = "XR_APILAYER_RIFTLIFT_ovr_compat"


def install_sdk_runtime(paths: Paths) -> Path:
    """Keep Meta's signed discovery DLL intact for the static Oculus SDK loader."""
    native = runtime_dir(paths)
    if (native / "LibOVRRT64_1.dll").is_file() and sha256(
        native / "LibOVRRT64_1.dll"
    ) == SDK_RUNTIME_SHA256:
        return native
    directory = paths.tools / "meta-runtime"
    target = directory / "LibOVRRT64_1.dll"
    if target.is_file() and sha256(target) == SDK_RUNTIME_SHA256:
        return directory
    package = download(
        "https://securecdn.oculus.com/binaries/download/?id=3766757683456363",
        paths.cache / "oculus-runtime.zip",
        "adbdc5f0285a2ac2ead6fdd34522de98de1bf6782017d9857ea4044b2d2fd009",
    )
    with zipfile.ZipFile(package) as bundle:
        payload = bundle.read(target.name)
    if hashlib.sha256(payload).hexdigest() != SDK_RUNTIME_SHA256:
        raise RiftLiftError("Meta SDK runtime checksum mismatch")
    atomic_write_bytes(target, payload)
    return directory


def runtime_dir(paths: Paths) -> Path:
    if override := os.environ.get("RIFTLIFT_WINDOWS_RUNTIME"):
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "native"
    return paths.tools / "windows-native" / RELEASE


def _check_platform_payload(target: Path) -> None:
    missing = sorted(name for name in PLATFORM_FILES if not (target / name).is_file())
    if missing:
        raise RiftLiftError(
            "Native bridge installed, but Meta platform compatibility is incomplete: "
            + ", ".join(missing)
            + ". Use a complete Windows package or a validated source-built runtime."
        )


def install_platform_dependencies(paths: Paths, target: Path) -> None:
    """Complete the pinned bridge with Meta's checksum-verified original DLLs."""
    dependencies = {
        "LibOVRPlatform64_1.dll": "LibOVRPlatform64_1.dll",
        "LibOVRP2P64_1.dll": "LibOVRP2P64_1.dll",
        "LibOVRRT64_1.dll": "LibOVRRT64_1.dll",
        "LibOVRPlatformImpl64_1_real.dll": "LibOVRPlatformImpl64_1.dll",
    }
    missing = {
        name: source
        for name, source in dependencies.items()
        if not (target / name).is_file()
    }
    if not missing:
        return
    package = download(
        "https://securecdn.oculus.com/binaries/download/?id=3766757683456363",
        paths.cache / "oculus-runtime.zip",
        "adbdc5f0285a2ac2ead6fdd34522de98de1bf6782017d9857ea4044b2d2fd009",
    )
    with zipfile.ZipFile(package) as bundle:
        # Read and validate before writing; never replace the compatibility shim
        # with Meta's original implementation of the same name.
        selected = {name: bundle.read(source) for name, source in missing.items()}
        sdk = bundle.read("LibOVRRT64_1.dll")
        if hashlib.sha256(sdk).hexdigest() != SDK_RUNTIME_SHA256:
            raise RiftLiftError("Meta SDK runtime checksum mismatch")
    for name, payload in selected.items():
        atomic_write_bytes(target / name, payload)


def install_payload(paths: Paths, archive: Path | None = None) -> Path:
    if getattr(sys, "frozen", False):
        target = runtime_dir(paths)
        if not all((target / name).is_file() for name in FILES | PLATFORM_FILES):
            raise RiftLiftError(
                "RiftLift's installation is incomplete. Please reinstall RiftLift."
            )
        return target
    if archive:
        payload = archive.read_bytes()
    else:
        with urllib.request.urlopen(PAYLOAD_URL, timeout=60) as response:
            payload = response.read(32 * 1024 * 1024 + 1)
    if hashlib.sha256(payload).hexdigest() != PAYLOAD_SHA256:
        raise RiftLiftError("Native payload SHA256 mismatch")
    target = runtime_dir(paths)
    # Validate the complete archive before writing. The pinned source payload
    # includes the bridge and platform shim; Meta supplies its original dependencies.
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
            if name in FILES | PLATFORM_FILES or (
                name.startswith("Input/") and name.endswith(".json")
            ):
                selected.append((relative, bundle.read(entry)))
        for relative, data in selected:
            dest = target.joinpath(*relative.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    (target / "local-build.json").unlink(missing_ok=True)
    install_platform_dependencies(paths, target)
    _check_platform_payload(target)
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
    config = Path(
        os.environ.get("VR_PATHREG_OVERRIDE")
        or str(Path(os.environ["LOCALAPPDATA"]) / "openvr/openvrpaths.vrpath")
    )
    try:
        roots = json.loads(config.read_text(encoding="utf-8"))["runtime"]
        for root in roots:
            candidate = Path(root)
            if any(
                (candidate / name).is_file()
                for name in ("bin/vrclient_x64.dll", "bin/win64/vrclient_x64.dll")
            ):
                return candidate
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def steamvr_openxr_manifest() -> Path | None:
    vr = active_openvr()
    manifest = vr / "steamxr_win64.json" if vr else None
    return manifest if manifest and manifest.is_file() else None


def openxr_environment(paths: Paths, environment: dict[str, str]) -> dict[str, str]:
    """Per-process OpenXR settings for the game; never touches the registry."""
    updates: dict[str, str] = {}
    # SteamVR users often never press "Set as OpenXR runtime". OpenVR-backed
    # games do not care, but OVRPlugin titles go straight to OpenXR.
    if (
        "XR_RUNTIME_JSON" not in environment
        and active_openxr() is None
        and (steamvr := steamvr_openxr_manifest())
    ):
        updates["XR_RUNTIME_JSON"] = str(steamvr)
    library = runtime_dir(paths) / OPENXR_LAYER_FILE
    if library.is_file():
        directory = paths.cache / "openxr-layer"
        manifest = directory / "riftlift-openxr-layer.json"
        payload = (
            json.dumps(
                {
                    "file_format_version": "1.0.0",
                    "api_layer": {
                        "name": OPENXR_LAYER_NAME,
                        # Older loaders bundled in games mis-resolve relative paths.
                        "library_path": str(library),
                        "api_version": "1.0",
                        "implementation_version": "1",
                        "description": "RiftLift OVRPlugin compatibility",
                    },
                },
                indent=2,
            )
            + "\n"
        )
        try:
            current = manifest.read_text(encoding="utf-8")
        except OSError:
            current = None
        if current != payload:
            directory.mkdir(parents=True, exist_ok=True)
            atomic_write_text(manifest, payload)
        for name, value in (
            ("XR_API_LAYER_PATH", str(directory)),
            ("XR_ENABLE_API_LAYERS", OPENXR_LAYER_NAME),
        ):
            existing = environment.get(name)
            updates[name] = f"{value}{os.pathsep}{existing}" if existing else value
    return updates


def runtime_ready(backend: str) -> bool:
    if backend not in {"openxr", "openvr"}:
        raise RiftLiftError("Unknown native backend")
    target = active_openxr() if backend == "openxr" else active_openvr()
    return bool(
        target and (target.is_file() if backend == "openxr" else target.is_dir())
    )


def select_backend(game: Game, requested: str = "auto") -> str:
    backend = requested
    if backend == "auto":
        if runtime_ready("openxr"):
            xr, vr = active_openxr(), active_openvr()
            # SteamVR's OpenXR path rejects Oculus cube and mipmapped textures.
            # Use its OpenVR interface when both registrations refer to SteamVR.
            if xr and vr and xr.resolve().parent == vr.resolve():
                return "openvr"
            return "openxr"
        return "openvr"
    if backend not in {"openxr", "openvr"}:
        raise RiftLiftError("Windows VR backend must be auto, openxr, or openvr")
    return backend


def doctor(paths: Paths) -> tuple[str, int]:
    native = runtime_dir(paths)
    payload_ok = all((native / name).is_file() for name in FILES)
    xr, vr = active_openxr(), active_openvr()
    installed = games(paths)
    platform_ok = all((native / name).is_file() for name in PLATFORM_FILES)
    platform_required = any(game.platform_shim for game in installed)
    text = "\n".join(
        [
            f"RiftLift {__version__} on Windows",
            f"Native payload: {'INSTALLED' if payload_ok else 'MISSING'} ({native})",
            f"Build: {'local source build' if (native / 'local-build.json').is_file() else 'runtime payload'}",
            f"OpenXR manifest: {xr or 'NOT REGISTERED'}",
            f"OpenVR runtime: {vr or 'NOT REGISTERED'}",
            f"Registered games: {len(installed)}",
            *(
                ["SIMULATED HEADSET: desktop rendering only; no physical headset test."]
                if os.environ.get("RIFTLIFT_SIMULATOR")
                else []
            ),
            *[
                f"  {game.name}: "
                f"{'FOUND' if game.executable_path.is_file() else 'EXECUTABLE MISSING'}"
                for game in installed
            ],
            f"Platform compatibility: {'INSTALLED' if platform_ok else 'MISSING'} (RiftLift offline mode)",
            "Headset rendering/input/audio: NOT VERIFIED",
            "Diagnostics remain local; no paste is uploaded.",
            "",
            "Next steps:",
            *([] if payload_ok else ["Install or repair the RiftLift native runtime."]),
            *(
                []
                if runtime_ready("openxr") or runtime_ready("openvr")
                else [
                    "Connect your headset and configure its Windows OpenXR runtime,",
                    "or install SteamVR in Steam and complete headset setup.",
                ]
            ),
            *(
                []
                if installed
                else [
                    "Install your owned PC games, then use Add Game to select their .exe files.",
                ]
            ),
            "RiftLift selects the Windows VR runtime automatically.",
            f"Game logs: {paths.data / 'logs'}",
        ]
    )
    return text, 0 if payload_ok and (platform_ok or not platform_required) and (
        runtime_ready("openxr") or runtime_ready("openvr")
    ) and all(game.executable_path.is_file() for game in installed) else 2


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


def _launcher_accepts_manifest(launcher: Path) -> bool:
    # The pinned source payload's launcher would treat /manifest as the game.
    return "/manifest".encode("utf-16-le") in launcher.read_bytes()


def steamvr_manifest(paths: Paths, game: Game) -> Path:
    """SteamVR app identity, so the dashboard shows the game rather than its .exe."""
    image = next(
        (
            game.artwork[name]
            for name in ("grid", "hero", "portrait")
            if name in game.artwork and Path(game.artwork[name]).is_file()
        ),
        None,
    )
    application = {
        "app_key": f"riftlift.app.{game.app_key}",
        "launch_type": "binary",
        "binary_path_windows": str(game.executable_path),
        "strings": {"en_us": {"name": game.name}},
        **({"image_path": image} if image else {}),
    }
    manifest = paths.cache / "steamvr" / f"{game.slug}.vrmanifest"
    payload = (
        json.dumps({"source": "user", "applications": [application]}, indent=2) + "\n"
    )
    try:
        current = manifest.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current != payload:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(manifest, payload)
    return manifest


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
    launcher = native / "RiftLiftLauncher.exe"
    identity = (
        ["/manifest", str(steamvr_manifest(paths, game))]
        if backend == "openvr" and _launcher_accepts_manifest(launcher)
        else []
    )
    return [
        str(launcher),
        f"/{backend}",
        "/wait",
        "/app",
        game.app_key,
        "/cwd",
        str(game.game_dir),
        *identity,
        str(executable),
        *game.arguments,
        *game.launch_options,
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
    # openxr_environment points the game at SteamVR's OpenXR runtime when no
    # other OpenXR runtime is registered, so that fallback counts as ready too.
    if not runtime_ready(backend) and not (
        backend == "openxr" and steamvr_openxr_manifest()
    ):
        raise RiftLiftError(
            f"Configure a Windows {backend} runtime and connect the headset first"
        )
    environment = os.environ.copy()
    if backend == "openvr":
        # Without its action manifest the runtime registers no SteamVR actions,
        # so SteamVR falls back to legacy input and games see no buttons.
        environment["RIFTLIFT_ACTION_MANIFEST"] = str(
            runtime_dir(paths) / "Input/action_manifest.json"
        )
    environment.update(game.environment)
    # Older Platform SDK loaders concatenate the DLL name directly to this value.
    environment["LIBOVR_DLL_DIR"] = str(install_sdk_runtime(paths)) + os.sep
    if game.platform_shim:
        native = runtime_dir(paths)
        if not all((native / name).is_file() for name in PLATFORM_FILES):
            raise RiftLiftError("RiftLift native platform compatibility DLL is missing")
        environment["PATH"] = str(native) + os.pathsep + environment.get("PATH", "")
        environment["RIFTLIFT_PLATFORM_DLL"] = str(
            native / "LibOVRPlatformImpl64_1.dll"
        )
        environment["LIBOVR_DLL_DIR"] = str(native) + os.sep
        # The shim's fallback ID is too small for engines that validate Oculus
        # IDs (Unreal treats IDs up to 100000 as invalid and fails login).
        environment["RIFTLIFT_USER_ID"] = platform_user_id(paths)
        if game.platform_offline:
            environment["RIFTLIFT_PLATFORM_OFFLINE"] = "1"
    environment.update(openxr_environment(paths, environment))
    if debug_logging_enabled(paths):
        environment["RIFTLIFT_RUNTIME_TRACE"] = "1"
    log = paths.data / "logs" / f"{game.slug}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    from .playtime import PlaytimeSession
    from .windows_process import run_game

    print(f"Launching {game.name} through Windows {backend}; log: {log}")
    with log.open("w", encoding="utf-8") as stream, PlaytimeSession(paths, game.slug):
        stream.write(f"{datetime.now().isoformat()} | {game.name} | {backend}\n")
        stream.write(f"Command: {subprocess.list2cmdline(command)}\n")
        stream.flush()
        returncode = run_game(
            command,
            cwd=game.game_dir,
            stdout=stream,
            env=environment,
        )
        stream.write(f"\nExit code: {returncode} (0x{returncode & 0xFFFFFFFF:08X})\n")
        launcher_log = (
            Path(os.environ["LOCALAPPDATA"]) / "RiftLift/RiftLiftLauncher.txt"
        )
        if launcher_log.is_file():
            evidence = launcher_log.read_text(encoding="utf-8", errors="replace")
            stream.write("\nNative launcher:\n" + evidence)
            if debug_logging_enabled(paths):
                print(evidence)
    print(f"Launcher exit: {returncode}; log: {log}")
    return returncode


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="riftlift",
        description="Run Meta Rift games through native Windows VR runtimes.",
    )
    p.add_argument("--home", type=Path, help=argparse.SUPPRESS)
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("gui", help="open the Windows library")
    simulator = sub.add_parser(
        "simulate", help="open RiftLift with SteamVR's simulated headset"
    )
    simulator.add_argument("--runtime", type=Path, required=True)
    sub.add_parser("login", help="sign in to Meta in your default browser")
    callback = sub.add_parser(
        "callback", help="receive a Meta browser sign-in callback"
    )
    callback.add_argument("url")
    download = sub.add_parser("add", help="download an owned Meta Rift PC game")
    download.add_argument("app")
    download.add_argument(
        "--build",
        help=(
            "specific version, version code, or binary ID, or 'all' to download "
            "every available version (see 'riftlift builds')"
        ),
    )
    download.add_argument("--executable")
    download.add_argument("--arguments")
    download.add_argument("--jobs", type=int)
    builds = sub.add_parser(
        "builds", help="list every version of an owned game you can download"
    )
    builds.add_argument("app", help="Meta Rift store URL or numeric app ID")
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
        "--backend", choices=["auto", "openxr", "openvr"], default="auto"
    )
    launch_parser.add_argument("--dry-run", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.home:
        os.environ["RIFTLIFT_HOME"] = str(args.home)
    paths = Paths.defaults()
    try:
        return _run_command(paths, args)
    except (
        OSError,
        ValueError,
        RiftLiftError,
        MetaApiError,
        DownloadError,
        zipfile.BadZipFile,
    ) as error:
        print(f"RiftLift: {error}", file=sys.stderr)
        return 1


def _run_command(paths: Paths, args: argparse.Namespace) -> int:
    if args.command in {"gui", "simulate"}:
        return _open_gui(paths, args)
    if args.command == "login":
        from .auth import login

        return login(paths)
    if args.command == "callback":
        from .auth import complete_login

        return complete_login(paths, args.url)
    if args.command in {"add", "builds"}:
        return _run_download(paths, args)
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
        game = add_local(paths, args.executable, args.name, args.root, args.arguments)
        print(f"Registered: {game.slug}")
    elif args.command == "launch":
        game = Game.load(paths, args.slug)
        return launch(paths, game, select_backend(game, args.backend), args.dry_run)
    return 0


def _run_download(paths: Paths, args: argparse.Namespace) -> int:
    from .builds import builds_table
    from .library import (
        ALL_BUILDS,
        add,
        add_all_versions,
        all_versions_summary,
        available_builds,
    )

    if args.command == "builds":
        print("\n".join(builds_table(available_builds(paths, args.app))))
        return 0
    if args.build == ALL_BUILDS:
        installed, failed = add_all_versions(paths, args.app, jobs=args.jobs)
        print("\n".join(all_versions_summary(installed, failed)))
        return 1 if failed else 0
    game = add(
        paths,
        args.app,
        build_selector=args.build,
        executable=args.executable,
        arguments=args.arguments,
        jobs=args.jobs,
    )
    print(f"Installed: {game.name} ({game.slug})")
    return 0


def _open_gui(paths: Paths, args: argparse.Namespace) -> int:
    if args.command == "simulate":
        from .windows_simulator import start

        start(paths, args.runtime)
    from .gui import main as gui

    return gui()


if __name__ == "__main__":
    raise SystemExit(main())
