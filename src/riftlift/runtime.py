from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .config import Paths, debug_logging_enabled
from .diagnostics import prepare_debug_logs
from .steam import steam_root
from .util import (
    RiftLiftError,
    atomic_write_bytes,
    atomic_write_text,
    download,
    run,
    sha256,
)
from .xr_runtime import (
    EnvisionProfile as EnvisionProfile,
)
from .xr_runtime import (
    _envision_version as _envision_version,
)
from .xr_runtime import (
    active_runtime_json as active_runtime_json,
)
from .xr_runtime import (
    envision_profile as envision_profile,
)
from .xr_runtime import (
    platform_user_id as platform_user_id,
)
from .xr_runtime import (
    xr_build_components as xr_build_components,
)

PROTON_VERSION = "GE-Proton11-3"
PROTON_URL = f"https://github.com/GloriousEggroll/proton-ge-custom/releases/download/{PROTON_VERSION}/{PROTON_VERSION}.tar.gz"
PROTON_SHA256 = "861c2edc8d40d051fb1e7a692deb953be52bd339c46d90f2b7dde50ddad91266"
DXVK_VERSION = "3.0.2-riftlift.1"
RELEASE_URL = (
    f"https://github.com/Villagers654/RiftLift/releases/download/v{__version__}"
)
DXVK_URL = f"{RELEASE_URL}/riftlift-dxvk.tar.gz"
DXVK_SHA256 = "15d2625b9a7f0d01f5096c17211ff8e98ba238ddc0d39de03bb58c2277d7eedc"
RUNTIME_VERSION = f"riftlift-{__version__}"
RUNTIME_URL = f"{RELEASE_URL}/riftlift-compat.zip"
RUNTIME_SHA256 = "49f3a588cd8e7feb59c2bf93719d4d234ac16371677c3ded66eb14f13a315985"
OPENVR_RUNTIME_VERSION = RUNTIME_VERSION
OPENVR_RUNTIME_URL = f"{RELEASE_URL}/riftlift-xrizer.tar.gz"
OPENVR_RUNTIME_SHA256 = (
    "f4a48981b88927b2c0c65d861b663fa851d306f5973b5f376d70ef7747217b5b"
)

DEBUG_WINE_CHANNELS = ",".join(
    (
        "+timestamp",
        "+pid",
        "+tid",
        "+seh",
        "+unwind",
        "+threadname",
        "+debugstr",
        "+loaddll",
        "+mscoree",
        "+process",
        "+wintrust",
        "+crypt",
        "+chain",
        "+openxr",
        "+vrclient",
        "+steamclient",
    )
)


def debug_logging_active(paths: Paths) -> bool:
    override = os.environ.get("RIFTLIFT_PROTON_LOG")
    return (
        override not in {"", "0"}
        if override is not None
        else debug_logging_enabled(paths)
    )


@dataclass(frozen=True, slots=True)
class MetaPackage:
    name: str
    binary_id: str
    sha256: str

    @property
    def url(self) -> str:
        return f"https://securecdn-atl3-3.oculus.com/binaries/download/?id={self.binary_id}"


META_PACKAGES = (
    MetaPackage(
        "oculus-runtime",
        "3766757683456363",
        "adbdc5f0285a2ac2ead6fdd34522de98de1bf6782017d9857ea4044b2d2fd009",
    ),
    MetaPackage(
        "oculus-platform-runtime",
        "28039291642329992",
        "69a6dedbf6f997459038e9b9e54d6562431c1a107bec17637d59626ab0d36892",
    ),
)
META_VERSION = "205.0"

META_RUNTIME_SIGNED_FILES = {
    "LibOVRRT32_1.dll": "e6435a297861f781d952ba21fdf009bc7d36fdaad0f96e0d39d3b419e1783983",
    "LibOVRRT64_1.dll": "f6941275692026b18666bb856d71fe1b19462017b2b2e556fe8df82461f493f5",
}
META_SIGNING_ROOT_THUMBPRINT = "0563B8630D62D75ABBC8AB1E4BDFB5A899B24D43"
META_SIGNING_ROOT_SUBJECT_KEY_ID = "45EBA2AFF492CB82312D518BA7A7219DF36DC80F"
META_SIGNING_ROOT_REGISTRY_KEY = (
    "HKLM\\Software\\Microsoft\\SystemCertificates\\Root\\Certificates\\"
    + META_SIGNING_ROOT_THUMBPRINT
)
META_SIGNING_ROOT_PEM = """-----BEGIN CERTIFICATE-----
MIIDtzCCAp+gAwIBAgIQDOfg5RfYRv6P5WD8G/AwOTANBgkqhkiG9w0BAQUFADBl
MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3
d3cuZGlnaWNlcnQuY29tMSQwIgYDVQQDExtEaWdpQ2VydCBBc3N1cmVkIElEIFJv
b3QgQ0EwHhcNMDYxMTEwMDAwMDAwWhcNMzExMTEwMDAwMDAwWjBlMQswCQYDVQQG
EwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3d3cuZGlnaWNl
cnQuY29tMSQwIgYDVQQDExtEaWdpQ2VydCBBc3N1cmVkIElEIFJvb3QgQ0EwggEi
MA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCtDhXO5EOAXLGH87dg+XESpa7c
JpSIqvTO9SA5KFhgDPiA2qkVlTJhPLWxKISKityfCgyDF3qPkKyK53lTXDGEKvYP
mDI2dsze3Tyoou9q+yHyUmHfnyDXH+Kx2f4YZNISW1/5WBg1vEfNoTb5a3/UsDg+
wRvDjDPZ2C8Y/igPs6eD1sNuRMBhNZYW/lmci3Zt1/GiSw0r/wty2p5g0I6QNcZ4
VYcgoc/lbQrISXwxmDNsIumH0DJaoroTghHtORedmTpyoeb6pNnVFzF1roV9Iq4/
AUaG9ih5yLHa5FcXxH4cDrC0kqZWs72yl+2qp/C3xag/lRbQ/6GW6whfGHdPAgMB
AAGjYzBhMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQW
BBRF66Kv9JLLgjEtUYunpyGd823IDzAfBgNVHSMEGDAWgBRF66Kv9JLLgjEtUYun
pyGd823IDzANBgkqhkiG9w0BAQUFAAOCAQEAog683+Lt8ONyc3pklL/3cmbYMuRC
dWKuh+vy1dneVrOfzM4UKLkNl2BcEkxY5NM9g0lFWJc1aRqoR+pWxnmrEthngYTf
fwk8lOa4JiwgvT2zKIn3X/8i4peEH+ll74fg38FnSbNd67IJKusm7Xi+fT8r87cm
NW1fiQG2SVufAQWbqz0lwcy2f8Lxb4bG+mRo64EtlOtCt/qMHt1i8b5QZ7dsvfPx
H2sMNgcWfzd8qVttevESRmCD1ycEvkvOl77DZypoEd+A5wwzZr8TDRRu838fYxAe
+o0bJW1sj6W3YQGx0qMmoRBxna3iw/nDmVG3KwcIzi7mULKn+gpFL6Lw8g==
-----END CERTIFICATE-----
"""


META_RUNTIME_PATCHES = {
    "OVRServiceLauncher.exe": {
        "input": "c83ac93d1698ba2459036c65806d3e713e3bf5a611c1996096aec1d8e7e3a8c8",
        "output": "68fb190b52195fbac3d1647e4667a1914db60ed7858b8522ffbbb2c696cbdbe9",
        "changes": ((11110, b"\x74", b"\xeb"),),
    },
    "OVRServer_x64.exe": {
        "input": "70ed0c064c806d7602b66cb3c70bd1c1c60f755409220d750799a653509b24a3",
        "output": "a2b2dc81f8713d1c3d03c38e379e31c5c3d345209be8f7ad781740207311af12",
        "changes": (
            (1472722, b"\x0f\x85\x09\x02\x00\x00", b"\x90\x90\x90\x90\x90\x90"),
            (1472754, b"\x0f\x84\xe9\x01\x00\x00", b"\xe9\xdd\x01\x00\x00\x90"),
        ),
    },
    "OculusAppFramework.dll": {
        "input": "c4b3a35269c34ff640cb87011751cd1ee7b04774570e73eae333fc1a60f097df",
        "output": "f0cf8cfe2f1bba513fab1e4d34a3cf2727705e8083a004b85f6608086d0b6a03",
        "changes": (
            (8731009, b"\x0f\x84\x92\x00\x00\x00", b"\xe9\x93\x00\x00\x00\x90"),
            (9208329, b"\x74", b"\xeb"),
        ),
    },
}


def _patch_meta_binary(target: Path, recipe: dict[str, object]) -> None:
    payload = bytearray(target.read_bytes())
    digest = hashlib.sha256(payload).hexdigest()
    if digest == recipe["output"]:
        return
    if digest != recipe["input"]:
        raise RiftLiftError(
            f"unsupported {target.name} build ({digest[:12]}); refusing an unsafe patch"
        )
    changes = recipe["changes"]
    if not isinstance(changes, tuple):
        raise RiftLiftError(f"invalid compatibility patch for {target.name}")
    for offset, before, after in changes:
        if payload[offset : offset + len(before)] != before:
            raise RiftLiftError(
                f"{target.name} compatibility patch did not match at {offset}"
            )
        payload[offset : offset + len(before)] = after
    if hashlib.sha256(payload).hexdigest() != recipe["output"]:
        raise RiftLiftError(
            f"{target.name} compatibility patch produced an unexpected result"
        )
    atomic_write_bytes(target, payload, mode=0o644)


def patch_meta_runtime(runtime: Path) -> None:
    for name, recipe in META_RUNTIME_PATCHES.items():
        _patch_meta_binary(runtime / name, recipe)

    plugins = runtime / "server-plugins"
    disabled = runtime / ".riftlift-disabled-server-plugins"
    for name in ("Rift.dll", "RiftS.dll"):
        source = plugins / name
        legacy = plugins / ".riftlift-disabled" / name
        if legacy.is_file() and not source.exists():
            source = legacy
        if source.is_file():
            disabled.mkdir(parents=True, exist_ok=True)
            shutil.move(source, disabled / name)
    legacy_dir = plugins / ".riftlift-disabled"
    if legacy_dir.is_dir() and not any(legacy_dir.iterdir()):
        legacy_dir.rmdir()


def _signed_meta_runtime_current(runtime: Path) -> bool:
    for name, expected in META_RUNTIME_SIGNED_FILES.items():
        target = runtime / name
        try:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            return False
        if digest != expected:
            return False
    return True


def _meta_signing_root_der() -> bytes:
    payload = "".join(
        line
        for line in META_SIGNING_ROOT_PEM.splitlines()
        if not line.startswith("-----")
    )
    return base64.b64decode(payload)


def _meta_signing_root_registry_blob() -> bytes:
    """Serialize the certificate properties used by Wine's registry store."""
    thumbprint = bytes.fromhex(META_SIGNING_ROOT_THUMBPRINT)
    subject_key_id = bytes.fromhex(META_SIGNING_ROOT_SUBJECT_KEY_ID)
    certificate = _meta_signing_root_der()

    def property_blob(identifier: int, value: bytes) -> bytes:
        return struct.pack("<III", identifier, 1, len(value)) + value

    return b"".join(
        (
            property_blob(3, thumbprint),
            property_blob(20, subject_key_id),
            property_blob(32, certificate),
        )
    )


def meta_signing_root_installed(paths: Paths) -> bool:
    """Check Wine's actual machine root store without starting Wine."""
    registry = paths.prefix / "pfx/system.reg"
    key = (
        r"[Software\\Microsoft\\SystemCertificates\\Root\\Certificates\\"
        + META_SIGNING_ROOT_THUMBPRINT
        + "]"
    )
    try:
        return key in registry.read_text(errors="replace")
    except OSError:
        return False


def _install_meta_signing_root(paths: Paths, support: Path) -> None:
    """Make Meta's LibOVR shim trust independent of the host distro CA set."""
    marker = support / ".riftlift-meta-signing-root-v2"
    runtime = support / "oculus-runtime"
    if marker.is_file() and meta_signing_root_installed(paths):
        return
    if not _signed_meta_runtime_current(runtime):
        return
    marker.unlink(missing_ok=True)
    # Wine's certutil accepts -addstore but can silently leave the store
    # unchanged on hosts that do not already trust this legacy root. Write the
    # certificate's serialized properties through Wine's registry API instead.
    proton(
        paths,
        "runinprefix",
        "reg.exe",
        "add",
        META_SIGNING_ROOT_REGISTRY_KEY,
        "/v",
        "Blob",
        "/t",
        "REG_BINARY",
        "/d",
        _meta_signing_root_registry_blob().hex(),
        "/f",
    )
    # Do not recreate the marker unless Wine can immediately read the entry.
    proton(
        paths,
        "runinprefix",
        "reg.exe",
        "query",
        META_SIGNING_ROOT_REGISTRY_KEY,
        "/v",
        "Blob",
    )
    marker.write_text(f"{META_SIGNING_ROOT_THUMBPRINT}\n")


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


def _safe_tar(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise RiftLiftError(f"unsafe path in {archive.name}: {member.name}")
            if member.issym() or member.islnk():
                raise RiftLiftError(
                    f"links are not allowed in {archive.name}: {member.name}"
                )
        source.extractall(destination, filter="data")


def install_proton(paths: Paths) -> Path:
    target = proton_dir()
    if (target / "proton").is_file():
        install_dxvk_compat(paths, target)
        return target
    archive = download(
        PROTON_URL, paths.cache / f"{PROTON_VERSION}.tar.gz", PROTON_SHA256
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as source:
        source.extractall(target.parent, filter="data")
    if not (target / "proton").is_file():
        raise RiftLiftError("GE-Proton archive did not contain the expected launcher")
    install_dxvk_compat(paths, target)
    return target


_DXVK_FILES = {
    "x64/d3d11.dll": "x86_64-windows/d3d11.dll",
    "x64/dxgi.dll": "x86_64-windows/dxgi.dll",
    "x32/d3d11.dll": "i386-windows/d3d11.dll",
    "x32/dxgi.dll": "i386-windows/dxgi.dll",
}


def _dxvk_current(marker: Path, destination: Path, artifact_sha256: str) -> bool:
    try:
        installed = json.loads(marker.read_text())
        installed_files = installed.get("files", {})
        return (
            installed.get("version") == DXVK_VERSION
            and installed.get("artifact_sha256") == artifact_sha256
            and all(
                installed_files.get(relative) == sha256(destination / relative)
                for relative in _DXVK_FILES.values()
            )
        )
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def _install_dxvk_files(source: Path, destination: Path) -> dict[str, str]:
    try:
        packaged_version = (source / "VERSION").read_text().strip()
    except OSError as error:
        raise RiftLiftError("RiftLift DXVK payload is incomplete") from error
    if packaged_version != DXVK_VERSION:
        raise RiftLiftError(
            f"RiftLift DXVK payload has unexpected version {packaged_version!r}"
        )
    file_hashes: dict[str, str] = {}
    for packaged, installed_path in _DXVK_FILES.items():
        try:
            payload = (source / packaged).read_bytes()
        except OSError as error:
            raise RiftLiftError("RiftLift DXVK payload is incomplete") from error
        if payload[:2] != b"MZ":
            raise RiftLiftError("RiftLift DXVK payload is incomplete")
        target = destination / installed_path
        atomic_write_bytes(target, payload, mode=0o644)
        file_hashes[installed_path] = sha256(target)
    return file_hashes


def install_dxvk_compat(paths: Paths, proton: Path) -> Path:
    destination = proton / "files/lib/wine/dxvk"
    marker = destination / ".riftlift-dxvk.json"
    override = os.environ.get("RIFTLIFT_DXVK_ARCHIVE")
    if override:
        archive = Path(override).expanduser()
        artifact_sha256 = sha256(archive)
    else:
        if not DXVK_SHA256:
            raise RiftLiftError("RiftLift DXVK release checksum is not configured")
        artifact_sha256 = DXVK_SHA256

    if _dxvk_current(marker, destination, artifact_sha256):
        return destination

    if not override:
        archive = download(
            DXVK_URL,
            paths.cache / f"dxvk-{DXVK_VERSION}.tar.gz",
            DXVK_SHA256,
        )

    paths.tools.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".dxvk-unpack-", dir=paths.tools))
    try:
        _safe_tar(archive, staging)
        source = staging / "dxvk"
        file_hashes = _install_dxvk_files(source, destination)
        atomic_write_text(
            marker,
            json.dumps(
                {
                    "version": DXVK_VERSION,
                    "artifact_sha256": artifact_sha256,
                    "files": file_hashes,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return destination


@dataclass(frozen=True, slots=True)
class NativeXrBridge:
    """The Wine PE/Unix pair that carries XR calls into the Linux process."""

    pe: Path
    unix: Path


def _contains_binary_marker(path: Path, marker: bytes) -> bool:
    overlap = len(marker) - 1
    tail = b""
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            payload = tail + chunk
            if marker in payload:
                return True
            tail = payload[-overlap:] if overlap else b""
    return False


def native_xr_bridge(proton: Path, backend: str) -> NativeXrBridge:
    """Resolve and validate GE-Proton's in-process native XR bridge.

    Oculus games must enter through a Windows DLL, but Linux OpenXR/OpenVR is
    reached through Wine's supported unixlib boundary in the same process.
    Refuse to launch if either half is absent or has the wrong binary format;
    silently falling back to a Windows-only runtime would violate RiftLift's
    native-runtime contract.
    """
    names = {
        "openxr": ("wineopenxr.dll", "wineopenxr.so"),
        "openvr": ("vrclient_x64.dll", "vrclient_x64.so"),
    }
    try:
        pe_name, unix_name = names[backend]
    except KeyError as error:
        raise RiftLiftError(f"unsupported XR backend: {backend}") from error
    wine = proton / "files/lib/wine"
    bridge = NativeXrBridge(
        wine / "x86_64-windows" / pe_name,
        wine / "x86_64-unix" / unix_name,
    )
    try:
        with bridge.pe.open("rb") as stream:
            pe_magic = stream.read(2)
        with bridge.unix.open("rb") as stream:
            unix_magic = stream.read(4)
    except OSError as error:
        raise RiftLiftError(
            f"GE-Proton is missing its native {backend.upper()} bridge: {error.filename}"
        ) from error
    if pe_magic != b"MZ" or unix_magic != b"\x7fELF":
        raise RiftLiftError(
            f"GE-Proton's native {backend.upper()} bridge has an invalid binary format"
        )
    if not _contains_binary_marker(
        bridge.pe, b"__wine_init_unix_call"
    ) or not _contains_binary_marker(bridge.unix, b"__wine_unix_call_funcs"):
        raise RiftLiftError(
            f"GE-Proton's {backend.upper()} files are not a Wine unixlib pair"
        )
    return bridge


def proton_environment(paths: Paths, game_dir: Path | None = None) -> dict[str, str]:
    root = steam_root()
    environment = os.environ.copy()
    debug_logging = debug_logging_active(paths)
    proton_log = "1" if debug_logging else "0"
    wine_debug_override = environment.get("RIFTLIFT_WINEDEBUG")
    wine_debug = wine_debug_override or (
        DEBUG_WINE_CHANNELS if debug_logging else "-all"
    )
    for variable in (
        "VR_OVERRIDE",
        "VR_PATHREG_OVERRIDE",
        "XR_RUNTIME_JSON",
        "PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES",
        "OXR_ZERO_TIME_IS_NOW",
        "RIFTLIFT_XRIZER",
    ):
        environment.pop(variable, None)
    environment.update(
        {
            "STEAM_COMPAT_DATA_PATH": str(paths.prefix),
            "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(root),
            "STEAM_COMPAT_INSTALL_PATH": str(game_dir or paths.data),
            "STEAM_COMPAT_LIBRARY_PATHS": str(root / "steamapps"),
            "SteamAppId": "0",
            "SteamGameId": "0",
            "PROTON_LOG": proton_log,
            "WINEDEBUG": wine_debug,
        }
    )
    if environment["PROTON_LOG"] != "0":
        logs = prepare_debug_logs(paths)
        environment.update(
            {
                "PROTON_LOG_DIR": str(logs["proton"]),
                "PROTON_CRASH_REPORT_DIR": str(logs["crashes"]),
                "DXVK_LOG_LEVEL": environment.get("DXVK_LOG_LEVEL", "debug"),
                "DXVK_LOG_PATH": str(logs["graphics"]),
                "VKD3D_DEBUG": environment.get("VKD3D_DEBUG", "info"),
                "VKD3D_SHADER_DEBUG": environment.get("VKD3D_SHADER_DEBUG", "warn"),
                "VK_LOADER_DEBUG": environment.get(
                    "VK_LOADER_DEBUG", "error,warn,info"
                ),
                "XR_LOADER_DEBUG": environment.get("XR_LOADER_DEBUG", "all"),
                "RUST_LOG": environment.get(
                    "RIFTLIFT_RUST_LOG", "info,xrizer_tracking=debug"
                ),
                "XRIZER_LOG_DIR": str(logs["openvr"]),
            }
        )
    return environment


def proton(
    paths: Paths, *arguments: str, game_dir: Path | None = None, **kwargs: object
) -> subprocess.CompletedProcess[str]:
    executable = install_proton(paths) / "proton"
    return run(
        (executable, *arguments), env=proton_environment(paths, game_dir), **kwargs
    )


def initialize_prefix(paths: Paths) -> Path:
    paths.create()
    if not (paths.prefix / "pfx/drive_c").is_dir():
        # Prefix maintenance must bypass Proton's Steam/UMU game launcher.
        # Using the game verb on a clean prefix can leave steam.exe helpers
        # alive and make setup wait forever after Wine has already initialized.
        proton(paths, "runinprefix", "cmd.exe", "/c", "exit")
    prefix = paths.prefix / "pfx"
    (
        prefix
        / "drive_c/users/steamuser/AppData/Roaming/Microsoft/Internet Explorer/Quick Launch"
    ).mkdir(parents=True, exist_ok=True)
    return prefix


def _install_meta_packages(paths: Paths, support: Path) -> None:
    for package in META_PACKAGES:
        archive = download(
            package.url, paths.cache / "meta" / f"{package.name}.pkg", package.sha256
        )
        destination = support / package.name
        marker = destination / ".riftlift-package.json"
        if marker.is_file():
            try:
                current_package = (
                    json.loads(marker.read_text()).get("sha256") == package.sha256
                )
                if package.name == "oculus-runtime":
                    current_package = current_package and _signed_meta_runtime_current(
                        destination
                    )
                if current_package:
                    continue
            except (OSError, json.JSONDecodeError):
                pass
        if destination.exists():
            shutil.rmtree(destination)
        _safe_zip(archive, destination)
        marker.write_text(
            json.dumps(
                {"binary_id": package.binary_id, "sha256": package.sha256}, indent=2
            )
            + "\n"
        )


def _registry_add(
    paths: Paths, key: str, name: str | None, kind: str, value: str
) -> None:
    name_arguments = ("/ve",) if name is None else ("/v", name)
    proton(
        paths,
        "runinprefix",
        "reg.exe",
        "add",
        key,
        *name_arguments,
        "/t",
        kind,
        "/d",
        value,
        "/f",
    )


def _configure_meta_registry(paths: Paths, support: Path) -> None:
    marker = support / ".riftlift-registry-v4"
    if marker.is_file():
        return
    base = r"C:\Program Files\Oculus"
    roots = (
        r"HKCU\Software\Oculus VR, LLC\Oculus",
        r"HKLM\Software\Oculus VR, LLC\Oculus",
        r"HKLM\Software\WOW6432Node\Oculus VR, LLC\Oculus",
    )
    for key in roots:
        _registry_add(paths, key, "Base", "REG_SZ", base)
        _registry_add(paths, key, "UseSystemProxy", "REG_DWORD", "0")
    for key in roots[1:]:
        _registry_add(paths, key, "Gestalt", "REG_DWORD", "1")

    config_roots = tuple(f"{key}\\Config" for key in roots)
    for key in config_roots:
        _registry_add(paths, key, "UseSystemProxy", "REG_DWORD", "0")
    wow_config = config_roots[-1]
    for name, value in (
        ("CldrLocaleCode", "en"),
        ("FbtLocaleCode", "en_US"),
        ("LanguageTag", "en-US"),
    ):
        _registry_add(paths, wow_config, name, "REG_SZ", value)
    _registry_add(paths, wow_config, "HomeDemoMode", "REG_DWORD", "0")

    service = r"HKLM\System\CurrentControlSet\Services\OVRService"
    for name, kind, value in (
        ("DisplayName", "REG_SZ", "Oculus VR Runtime Service"),
        ("Description", "REG_SZ", "Oculus VR Runtime Service"),
        (
            "ImagePath",
            "REG_EXPAND_SZ",
            r'"C:\Program Files\Oculus\Support\oculus-runtime\OVRServiceLauncher.exe"',
        ),
        ("ObjectName", "REG_SZ", "LocalSystem"),
        ("Type", "REG_DWORD", "16"),
        ("Start", "REG_DWORD", "4"),
        ("ErrorControl", "REG_DWORD", "1"),
    ):
        _registry_add(paths, service, name, kind, value)
    marker.write_text("1\n")


def install_meta_runtime(paths: Paths) -> Path:
    prefix = initialize_prefix(paths)
    support = prefix / "drive_c/Program Files/Oculus/Support"
    shutil.rmtree(support / "oculus-client", ignore_errors=True)
    _install_meta_packages(paths, support)
    patch_meta_runtime(support / "oculus-runtime")
    _install_meta_signing_root(paths, support)
    _configure_meta_registry(paths, support)
    return support


def install_rift_runtime(paths: Paths) -> Path:
    destination = paths.tools / "rift-runtime"
    version_marker = destination / ".riftlift-version"
    required = (
        "RiftLiftLauncher.exe",
        "RiftLiftOpenXR64.dll",
        "RiftLiftOpenVR64.dll",
        "openvr_api64.dll",
        "LibOVRPlatformImpl64_1.dll",
        "Input/action_manifest.json",
        "Input/gamepad_default.json",
        "Input/holographic_controller_default.json",
        "Input/knuckles_default.json",
        "Input/oculus_touch_default.json",
        "Input/vive_controller_default.json",
        "Input/vive_cosmos_default.json",
    )
    if (
        all((destination / name).is_file() for name in required)
        and version_marker.is_file()
        and version_marker.read_text().strip() == RUNTIME_VERSION
    ):
        return destination
    override = os.environ.get("RIFTLIFT_RUNTIME_ARCHIVE")
    archive = (
        Path(override).expanduser()
        if override
        else download(
            RUNTIME_URL,
            paths.cache / f"riftlift-compat-{RUNTIME_VERSION}.zip",
            RUNTIME_SHA256,
        )
    )
    if destination.exists():
        shutil.rmtree(destination)
    _safe_zip(archive, destination)
    nested = destination / "riftlift-runtime"
    if nested.is_dir() and not (destination / "RiftLiftLauncher.exe").exists():
        for item in nested.iterdir():
            shutil.move(str(item), destination / item.name)
        nested.rmdir()
    if not all((destination / name).is_file() for name in required):
        raise RiftLiftError("RiftLift runtime payload is incomplete")
    version_marker.write_text(f"{RUNTIME_VERSION}\n")
    return destination


def install_openvr_runtime(paths: Paths) -> Path:
    """Install RiftLift's native OpenVR-to-OpenXR implementation."""
    destination = paths.tools / "openvr-runtime"
    library = destination / "libxrizer.so"
    proton_library = destination / "bin/linux64/vrclient.so"
    version_marker = destination / ".riftlift-version"
    if (
        library.is_file()
        and proton_library.is_file()
        and version_marker.is_file()
        and version_marker.read_text().strip() == OPENVR_RUNTIME_VERSION
    ):
        _write_openvr_path_registry(paths, destination)
        return destination
    override = os.environ.get("RIFTLIFT_OPENVR_RUNTIME_ARCHIVE")
    if override:
        archive = Path(override).expanduser()
    else:
        if not OPENVR_RUNTIME_SHA256:
            raise RiftLiftError(
                "RiftLift OpenVR runtime release checksum is not configured"
            )
        archive = download(
            OPENVR_RUNTIME_URL,
            paths.cache / f"openvr-runtime-{OPENVR_RUNTIME_VERSION}.tar.gz",
            OPENVR_RUNTIME_SHA256,
        )
    if destination.exists():
        shutil.rmtree(destination)
    staging = paths.tools / ".openvr-runtime-unpack"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        _safe_tar(archive, staging)
        nested = staging / "xrizer"
        source = nested if nested.is_dir() else staging
        if not (source / "libxrizer.so").is_file():
            raise RiftLiftError("RiftLift OpenVR runtime payload is incomplete")
        source.replace(destination)
        if staging.exists():
            shutil.rmtree(staging)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    proton_library.parent.mkdir(parents=True, exist_ok=True)
    # Proton's VR_OVERRIDE contract is a SteamVR-shaped runtime directory,
    # not a direct shared-library path. Keep one real payload and expose it at
    # the standard vrclient location without relying on archive symlinks.
    try:
        os.link(library, proton_library)
    except OSError:
        shutil.copy2(library, proton_library)
    (destination / "bin/version.txt").write_text(f"{OPENVR_RUNTIME_VERSION}\n")
    version_marker.write_text(f"{OPENVR_RUNTIME_VERSION}\n")
    _write_openvr_path_registry(paths, destination)
    return destination


def steamvr_runtime_for_openxr(runtime: Path) -> Path | None:
    """Return SteamVR's root when *runtime* is Valve's OpenXR manifest."""
    try:
        payload = json.loads(runtime.read_text())
        description = payload["runtime"]
        is_steamvr = (
            description.get("VALVE_runtime_is_steamvr") is True
            or str(description.get("name", "")).casefold() == "steamvr"
        )
    except (OSError, json.JSONDecodeError, KeyError, AttributeError):
        return None
    if not is_steamvr:
        return None
    root = runtime.resolve().parent
    if not (root / "bin/linux64/vrclient.so").is_file():
        return None
    return root


def select_openvr_runtime(
    paths: Paths, openxr_runtime: Path | None = None
) -> tuple[Path, Path, str]:
    """Select a direct OpenVR target matching the active headset runtime.

    An explicit ``VR_OVERRIDE`` remains authoritative. When SteamVR is the
    active OpenXR runtime, use Valve's OpenVR client directly. Monado and other
    OpenXR runtimes continue through RiftLift's bundled XRizer translator.
    """
    explicit = os.environ.get("VR_OVERRIDE", "").strip()
    registry_override = os.environ.get("VR_PATHREG_OVERRIDE", "").strip()
    if explicit:
        runtime = Path(explicit).expanduser().resolve()
        if not (runtime / "bin/linux64/vrclient.so").is_file():
            raise RiftLiftError(
                f"VR_OVERRIDE is not a usable OpenVR runtime: {runtime}"
            )
        selected_registry = (
            Path(registry_override).expanduser().resolve()
            if registry_override
            else _write_openvr_path_registry(paths, runtime)
        )
        registry = _private_openvr_path_registry(paths, selected_registry)
        if steamvr_runtime_for_openxr(runtime / "steamxr_linux64.json") == runtime:
            kind = "steamvr"
        elif (runtime / "libxrizer.so").is_file():
            kind = "xrizer"
        else:
            kind = "external"
        return runtime, registry, kind

    # Resolve OpenXR only after honoring an explicit OpenVR target. A native
    # OpenVR runtime is self-contained, and requiring an unrelated OpenXR
    # registration first makes otherwise valid SteamVR launches fail on fresh
    # systems. XRizer still needs the active OpenXR runtime below.
    openxr_runtime = openxr_runtime or active_runtime_json()
    steamvr = steamvr_runtime_for_openxr(openxr_runtime)
    if steamvr is not None:
        selected_registry = (
            Path(registry_override).expanduser().resolve()
            if registry_override
            else _write_openvr_path_registry(paths, steamvr)
        )
        registry = _private_openvr_path_registry(paths, selected_registry)
        return steamvr, registry, "steamvr"

    xrizer = install_openvr_runtime(paths)
    return xrizer, _write_openvr_path_registry(paths, xrizer), "xrizer"


def _write_openvr_path_registry(paths: Paths, runtime: Path) -> Path:
    """Provide Proton the valid path registry required to consume VR_OVERRIDE."""
    target = paths.config / "openvr/openvrpaths.vrpath"
    steam_install = runtime.parent.parent.parent
    if (runtime / "steamxr_linux64.json").is_file() and (
        steam_install / "config"
    ).is_dir():
        # SteamVR's settings and server logs belong to Steam. The registry file
        # itself remains private to RiftLift so selecting SteamVR never edits a
        # user's global OpenVR registration.
        config = steam_install / "config"
        logs = steam_install / "logs"
    else:
        config = paths.config / "openvr/runtime"
        logs = prepare_debug_logs(paths)["openvr"]
    target.parent.mkdir(parents=True, exist_ok=True)
    config.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        target,
        json.dumps(
            {
                "version": 1,
                "runtime": [str(runtime)],
                "config": [str(config)],
                "log": [str(logs)],
            },
            indent=2,
        )
        + "\n",
    )
    return target


def _private_openvr_path_registry(paths: Paths, source: Path) -> Path:
    """Mirror an explicit OpenVR registry where native Proton clients find it."""
    target = paths.config / "openvr/openvrpaths.vrpath"
    if source == target.resolve():
        return target
    try:
        payload = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise RiftLiftError(
            f"OpenVR path registry is not readable: {source}"
        ) from error
    if not isinstance(payload, dict):
        raise RiftLiftError(f"OpenVR path registry is invalid: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(payload, indent=2) + "\n")
    return target


def install_platform_compat(paths: Paths) -> Path:
    source = install_meta_runtime(paths) / "oculus-runtime"
    destination = paths.tools / "platform-compat"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("LibOVRPlatform64_1.dll", "LibOVRP2P64_1.dll"):
        if (source / name).is_file():
            shutil.copy2(source / name, destination / name)
    runtime_impl = source / "LibOVRPlatformImpl64_1.dll"
    runtime_real = source / "LibOVRPlatformImpl64_1_real.dll"
    # The public Platform DLL resolves its implementation from Meta's installed
    # runtime directory even when loaded from LIBOVR_DLL_DIR. Preserve the
    # vendor implementation there once, then put our forwarding shim at the
    # canonical name; otherwise legacy clients bypass the compatibility layer.
    if runtime_impl.is_file() and not runtime_real.is_file():
        shutil.copy2(runtime_impl, runtime_real)
    if runtime_real.is_file():
        shutil.copy2(runtime_real, destination / "LibOVRPlatformImpl64_1_real.dll")
    override = os.environ.get("RIFTLIFT_PLATFORM_SHIM")
    bundled = paths.tools / "rift-runtime" / "LibOVRPlatformImpl64_1.dll"
    shim = Path(override).expanduser() if override else bundled
    if not shim.is_file():
        raise RiftLiftError(
            "RiftLift platform compatibility DLL is missing from the release payload"
        )
    shutil.copy2(shim, destination / "LibOVRPlatformImpl64_1.dll")
    shutil.copy2(shim, runtime_impl)
    return destination


def shutdown_compat_prefix(paths: Paths, proton_root: Path) -> None:
    """Leave setup's shared prefix idle for the first game launch.

    Wine's desktop and service processes deliberately persist after registry and
    package installation.  Proton's first ``run`` can otherwise attach while
    that bootstrap server is still active and stall in ``umu.exe`` before the
    requested executable is created.
    """
    wineserver = proton_root / "files/bin/wineserver"
    if not wineserver.is_file():
        raise RiftLiftError("GE-Proton is missing wineserver after installation")
    environment = os.environ.copy()
    environment.update(
        {
            "WINEPREFIX": str(paths.prefix / "pfx"),
            "WINEDEBUG": "-all",
        }
    )
    environment.pop("LD_PRELOAD", None)
    try:
        result = subprocess.run(
            [str(wineserver), "-k", "-w"],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RiftLiftError(
            f"could not stop the compatibility prefix after setup: {error}"
        ) from error
    # Wine returns 1 when no server exists for this prefix.  That is already
    # the desired post-setup state, not a cleanup failure.
    if result.returncode not in (0, 1):
        raise RiftLiftError(
            "could not stop the compatibility prefix after setup "
            f"(wineserver exit {result.returncode})"
        )


def setup(paths: Paths) -> None:
    paths.create()
    proton_root = install_proton(paths)
    install_meta_runtime(paths)
    install_rift_runtime(paths)
    install_openvr_runtime(paths)
    install_platform_compat(paths)
    shutdown_compat_prefix(paths, proton_root)


def launch_environment(
    paths: Paths,
    game_dir: Path,
    platform_shim: bool,
    platform_offline: bool = False,
    *,
    runtime: Path | None = None,
) -> dict[str, str]:
    runtime = runtime or active_runtime_json()
    environment = proton_environment(paths, game_dir)
    existing_overrides = environment.get("WINEDLLOVERRIDES", "").strip(";")
    environment.update(
        {
            "XR_RUNTIME_JSON": str(runtime),
            "PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES": "1",
            "OXR_ZERO_TIME_IS_NOW": "1",
            "WINEDLLOVERRIDES": f"d3d11=n;dxgi=n{';' + existing_overrides if existing_overrides else ''}",
        }
    )
    if platform_shim:
        install_platform_compat(paths)
        meta_runtime_win = r"C:\Program Files\Oculus\Support\oculus-runtime"
        # Keep one canonical public Platform SDK DLL visible. The vendor
        # loader rejects initialization when its already-loaded DLL path does
        # not match the runtime path resolved from the Oculus installation.
        environment["WINEPATH"] = meta_runtime_win
        environment["RIFTLIFT_USER_ID"] = platform_user_id(paths)
        if platform_offline:
            environment["RIFTLIFT_PLATFORM_OFFLINE"] = "1"
    return environment
