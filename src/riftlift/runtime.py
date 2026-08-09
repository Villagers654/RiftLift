from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .config import Paths
from .util import RiftLiftError, download, linux_to_windows, run


PROTON_VERSION = "GE-Proton11-3"
PROTON_URL = f"https://github.com/GloriousEggroll/proton-ge-custom/releases/download/{PROTON_VERSION}/{PROTON_VERSION}.tar.gz"
PROTON_SHA256 = "861c2edc8d40d051fb1e7a692deb953be52bd339c46d90f2b7dde50ddad91266"
REVIVE_VERSION = "riftlift-0.1.0"
REVIVE_URL = "https://github.com/Villagers654/RiftLift/releases/download/v0.1.0/riftlift-compat.zip"


@dataclass(frozen=True, slots=True)
class MetaPackage:
    name: str
    binary_id: str
    sha256: str

    @property
    def url(self) -> str:
        return f"https://securecdn-atl3-3.oculus.com/binaries/download/?id={self.binary_id}"


# These are current Meta Horizon Link 205.0 packages. They are pinned because
# setup must be reproducible; `riftlift doctor` reports when newer metadata is
# available rather than silently changing a working prefix.
META_PACKAGES = (
    MetaPackage("oculus-runtime", "3766757683456363", "adbdc5f0285a2ac2ead6fdd34522de98de1bf6782017d9857ea4044b2d2fd009"),
    MetaPackage("oculus-client", "3476193069202515", "e87446a3c828912aea47db2bfc4113d3676f570731d5e854619859ab127f737e"),
    MetaPackage("oculus-platform-runtime", "28039291642329992", "69a6dedbf6f997459038e9b9e54d6562431c1a107bec17637d59626ab0d36892"),
)


def steam_root() -> Path:
    for candidate in (
        Path.home() / ".local/share/Steam",
        Path.home() / ".steam/steam",
        Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam",
    ):
        if candidate.is_dir():
            return candidate.resolve()
    raise RiftLiftError("Steam was not found; install and start Steam once, then retry")


def proton_dir() -> Path:
    return steam_root() / "compatibilitytools.d" / PROTON_VERSION


def _safe_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            target = (destination / member.filename.replace("\\", "/")).resolve()
            if target != root and root not in target.parents:
                raise RiftLiftError(f"unsafe path in {archive.name}: {member.filename}")
        source.extractall(destination)


def install_proton(paths: Paths) -> Path:
    target = proton_dir()
    if (target / "proton").is_file():
        return target
    archive = download(PROTON_URL, paths.cache / f"{PROTON_VERSION}.tar.gz", PROTON_SHA256)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as source:
        source.extractall(target.parent, filter="data")
    if not (target / "proton").is_file():
        raise RiftLiftError("GE-Proton archive did not contain the expected launcher")
    return target


def proton_environment(paths: Paths, game_dir: Path | None = None) -> dict[str, str]:
    root = steam_root()
    environment = os.environ.copy()
    environment.update(
        {
            "STEAM_COMPAT_DATA_PATH": str(paths.prefix),
            "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(root),
            "STEAM_COMPAT_INSTALL_PATH": str(game_dir or paths.data),
            "STEAM_COMPAT_LIBRARY_PATHS": str(root / "steamapps"),
            "SteamAppId": "0",
            "SteamGameId": "0",
            "PROTON_LOG": environment.get("RIFTLIFT_PROTON_LOG", "0"),
            "WINEDEBUG": environment.get("RIFTLIFT_WINEDEBUG", "-all"),
        }
    )
    return environment


def proton(paths: Paths, *arguments: str, game_dir: Path | None = None, **kwargs: object) -> subprocess.CompletedProcess[str]:
    executable = install_proton(paths) / "proton"
    return run((executable, *arguments), env=proton_environment(paths, game_dir), **kwargs)


def initialize_prefix(paths: Paths) -> Path:
    paths.create()
    if not (paths.prefix / "pfx/drive_c").is_dir():
        proton(paths, "run", "cmd.exe", "/c", "exit")
    return paths.prefix / "pfx"


def install_meta_runtime(paths: Paths) -> Path:
    prefix = initialize_prefix(paths)
    support = prefix / "drive_c/Program Files/Oculus/Support"
    for package in META_PACKAGES:
        archive = download(package.url, paths.cache / "meta" / f"{package.name}.pkg", package.sha256)
        destination = support / package.name
        marker = destination / ".riftlift-package.json"
        if marker.is_file():
            try:
                if json.loads(marker.read_text()).get("sha256") == package.sha256:
                    continue
            except (OSError, json.JSONDecodeError):
                pass
        if destination.exists():
            shutil.rmtree(destination)
        _safe_zip(archive, destination)
        marker.write_text(json.dumps({"binary_id": package.binary_id, "sha256": package.sha256}, indent=2) + "\n")

    base = r"C:\Program Files\Oculus"
    for key in (
        r"HKCU\Software\Oculus VR, LLC\Oculus",
        r"HKLM\Software\Oculus VR, LLC\Oculus",
        r"HKLM\Software\WOW6432Node\Oculus VR, LLC\Oculus",
    ):
        proton(paths, "run", "reg.exe", "add", key, "/v", "Base", "/t", "REG_SZ", "/d", base, "/f")
    protocol = r'"C:\Program Files\Oculus\Support\oculus-client\Client.exe" -- --url "%1"'
    proton(paths, "run", "reg.exe", "add", r"HKCU\Software\Classes\oculus\shell\open\command", "/ve", "/t", "REG_SZ", "/d", protocol, "/f")
    return support


def install_revive(paths: Paths) -> Path:
    destination = paths.tools / "revive"
    if (destination / "ReviveInjector.exe").is_file() and (destination / "LibReviveXR64.dll").is_file():
        return destination
    override = os.environ.get("RIFTLIFT_REVIVE_ARCHIVE")
    archive = Path(override).expanduser() if override else download(REVIVE_URL, paths.cache / "riftlift-revive.zip")
    if destination.exists():
        shutil.rmtree(destination)
    _safe_zip(archive, destination)
    nested = destination / "riftlift-revive"
    if nested.is_dir() and not (destination / "ReviveInjector.exe").exists():
        for item in nested.iterdir():
            shutil.move(str(item), destination / item.name)
        nested.rmdir()
    if not (destination / "ReviveInjector.exe").is_file() or not (destination / "LibReviveXR64.dll").is_file():
        raise RiftLiftError("Revive payload is incomplete")
    return destination


def install_platform_compat(paths: Paths) -> Path:
    source = install_meta_runtime(paths) / "oculus-platform-runtime"
    destination = paths.tools / "platform-compat"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("LibOVRPlatform64_1.dll", "LibOVRP2P64_1.dll"):
        if (source / name).is_file():
            shutil.copy2(source / name, destination / name)
    real = source / "LibOVRPlatformImpl64_1.dll"
    if real.is_file():
        shutil.copy2(real, destination / "LibOVRPlatformImpl64_1_real.dll")
    override = os.environ.get("RIFTLIFT_PLATFORM_SHIM")
    bundled = paths.tools / "revive" / "LibOVRPlatformImpl64_1.dll"
    shim = Path(override).expanduser() if override else bundled
    if not shim.is_file():
        raise RiftLiftError("RiftLift platform compatibility DLL is missing from the release payload")
    shutil.copy2(shim, destination / "LibOVRPlatformImpl64_1.dll")
    return destination


def setup(paths: Paths) -> None:
    paths.create()
    install_proton(paths)
    install_meta_runtime(paths)
    install_revive(paths)
    install_platform_compat(paths)


def login(paths: Paths) -> int:
    support = install_meta_runtime(paths)
    client = support / "oculus-client/Client.exe"
    print("Sign in in the Meta Horizon Link window. RiftLift keeps this shared prefix for future games.")
    return proton(paths, "run", str(client)).returncode


def active_runtime_json() -> Path:
    explicit = os.environ.get("XR_RUNTIME_JSON")
    candidates = [Path(explicit)] if explicit else []
    candidates.extend(
        (
            Path.home() / ".config/openxr/1/active_runtime.json",
            Path("/usr/share/openxr/1/openxr_monado.json"),
            Path("/usr/local/share/openxr/1/openxr_monado.json"),
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RiftLiftError("no active Linux OpenXR runtime was found; install/start Monado and retry")


def launch_environment(paths: Paths, game_dir: Path, platform_shim: bool, platform_offline: bool = False) -> dict[str, str]:
    environment = proton_environment(paths, game_dir)
    runtime = active_runtime_json()
    environment.update(
        {
            "XR_RUNTIME_JSON": str(runtime),
            "PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES": "1",
            "OXR_ZERO_TIME_IS_NOW": "1",
            "RIFTLIFT_XRIZER": "1",
        }
    )
    if platform_shim:
        compatibility = install_platform_compat(paths)
        runtime_dir = paths.prefix / "pfx/drive_c/Program Files/Oculus/Support/oculus-runtime"
        compatibility_win = linux_to_windows(compatibility)
        environment["LIBOVR_DLL_DIR"] = compatibility_win
        environment["WINEPATH"] = f"{compatibility_win};{linux_to_windows(runtime_dir)}"
        if platform_offline:
            environment["RIFTLIFT_PLATFORM_OFFLINE"] = "1"
    return environment
