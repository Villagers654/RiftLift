from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import struct
import subprocess
import tarfile
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .auth import complete_browser_login, sign_out
from .auth_browser import default_browser, launch_browser_login, stop_browser
from .config import Paths, debug_logging_enabled
from .diagnostics import prepare_debug_logs
from .meta_auth import MetaAuthSession, install_protocol_handler, record_callback
from .util import RiftLiftError, download, linux_to_windows, run

PROTON_VERSION = "GE-Proton11-3"
PROTON_URL = f"https://github.com/GloriousEggroll/proton-ge-custom/releases/download/{PROTON_VERSION}/{PROTON_VERSION}.tar.gz"
PROTON_SHA256 = "861c2edc8d40d051fb1e7a692deb953be52bd339c46d90f2b7dde50ddad91266"
RUNTIME_VERSION = "riftlift-0.10.0"
RUNTIME_URL = "https://github.com/Villagers654/RiftLift/releases/download/v0.10.0/riftlift-compat.zip"
RUNTIME_SHA256 = "0bfc24cd4c893a7dc257da7b22d190e3e1449f863f623d9550f3f11941350b85"
OPENVR_RUNTIME_VERSION = "riftlift-0.10.0"
OPENVR_RUNTIME_URL = "https://github.com/Villagers654/RiftLift/releases/download/v0.10.0/riftlift-xrizer.tar.gz"
OPENVR_RUNTIME_SHA256 = (
    "e627d746a46246289580e5e3fb0b53527fc84cb4fedb3505261639e713d59247"
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


@dataclass(frozen=True, slots=True)
class EnvisionProfile:
    """The Envision profile whose Monado build is selected by the user."""

    uuid: str
    name: str
    prefix: Path
    manifest: Path
    environment: dict[str, str]


META_PACKAGES = (
    MetaPackage(
        "oculus-runtime",
        "3766757683456363",
        "adbdc5f0285a2ac2ead6fdd34522de98de1bf6782017d9857ea4044b2d2fd009",
    ),
    MetaPackage(
        "oculus-client",
        "3476193069202515",
        "e87446a3c828912aea47db2bfc4113d3676f570731d5e854619859ab127f737e",
    ),
    MetaPackage(
        "oculus-platform-runtime",
        "28039291642329992",
        "69a6dedbf6f997459038e9b9e54d6562431c1a107bec17637d59626ab0d36892",
    ),
)

META_CLIENT_COMPAT_MARKER = ".riftlift-client-compat-v11"

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


def patch_meta_runtime(runtime: Path) -> None:
    """Apply tightly pinned Wine compatibility fixes to Meta 205.

    The official binaries reject Wine's service context, OAF COM setup, and
    Authenticode result before the actual runtime can initialize. Every edit is
    guarded by both the complete input/output SHA-256 and exact original bytes,
    so a Meta update fails closed instead of patching an unknown executable.
    """
    for name, recipe in META_RUNTIME_PATCHES.items():
        target = runtime / name
        payload = bytearray(target.read_bytes())
        digest = hashlib.sha256(payload).hexdigest()
        if digest == recipe["output"]:
            continue
        if digest != recipe["input"]:
            raise RiftLiftError(
                f"unsupported {name} build ({digest[:12]}); refusing an unsafe patch"
            )
        for offset, before, after in recipe["changes"]:
            if payload[offset : offset + len(before)] != before:
                raise RiftLiftError(
                    f"{name} compatibility patch did not match at {offset}"
                )
            payload[offset : offset + len(before)] = after
        if hashlib.sha256(payload).hexdigest() != recipe["output"]:
            raise RiftLiftError(
                f"{name} compatibility patch produced an unexpected result"
            )
        temporary = target.with_suffix(target.suffix + ".riftlift.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, target)

    plugins = runtime / "server-plugins"
    # Keep quarantined DLLs outside server-plugins: Meta scans that tree
    # recursively, including dot-prefixed directories.
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


def patch_meta_client(client: Path) -> None:
    """Apply deterministic installer and Wine IPC compatibility fixes."""
    resources = client / "resources"
    casting_source = resources / "bin/Casting"
    casting_target = resources / "app.asar.unpacked/bin/Casting"
    if casting_source.is_dir() and not casting_target.exists():
        casting_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(casting_source, casting_target)

    # Meta's Electron window starts hidden and waits for an OAF renderer signal.
    # Under Wine that signal can arrive only after the visible sign-in flow, a
    # deadlock. Change the already-supported policy to show on DOM-ready. Keep
    # the ASAR byte length unchanged so its file offsets and integrity stay valid.
    archive = resources / "app.asar"
    marker = client / META_CLIENT_COMPAT_MARKER
    if marker.is_file():
        return
    payload = archive.read_bytes()
    replacements = (
        (b'showBehavior:"whenSignaled"', b'showBehavior:"whenLoaded"  ', 2),
        (
            b'show:!1,title:"Meta Horizon Link Client"',
            b'show:!0,title:"Meta Horizon Link Client"',
            2,
        ),
        # OAF FastIPC never returns /auth/gettoken under Wine. Keep the token in
        # RiftLift's isolated Electron profile, which persists with the prefix.
        (
            b'fetchAccessToken:async()=>(0,i.default)(r.default.GET_AUTH_TOKEN),invalidateAccessToken:async()=>{throw new Error("invalidateAccessToken not implemented")}',
            b'fetchAccessToken:async()=>localStorage.getItem("riftlift-token")||"",invalidateAccessToken:async()=>0                                                      ',
            3,
        ),
        # Proton has a valid built-in en-US locale, but OAF's language request
        # hangs for a fresh prefix. Initialize FBT with Meta's own compiled-in
        # locale before releasing readiness; this preserves every translation
        # invariant without depending on the broken IPC response.
        (
            b't.initialize=async()=>{const e=await r.default.fetchLanguageTag(f);f(e,"never",!0),h.set()}',
            b't.initialize=()=>{f(o.LANGUAGE_TAG,"never",!0),h.set()}                                    ',
            2,
        ),
        # OAF consumes these token writes but its Wine FastIPC server does not
        # acknowledge them. Do not deadlock the login state machine waiting for
        # acknowledgements; the calls still run and the primary access token is
        # also committed to the renderer's token store immediately afterward.
        (
            b"t.setAccessToken=async(e,t)=>{await(0,d.default)(c.default.SET_AUTH_TOKEN,{token:e}),r.default.set(e),t.initialize&&await(0,p.initialize)(!0)};",
            b't.setAccessToken=async(e,t)=>{localStorage.setItem("riftlift-token",e),r.default.set(e),t.initialize&&await(0,p.initialize)(!0)};              ',
            1,
        ),
        (
            b"await(0,d.default)(c.default.SET_FRL_AUTH_TOKEN,{token:e})",
            b"void (0,d.default)(c.default.SET_FRL_AUTH_TOKEN,{token:e})",
            1,
        ),
        (
            b"await(0,d.default)(c.default.SET_AUTH_META_TOKEN,{metatoken:e})",
            b"void (0,d.default)(c.default.SET_AUTH_META_TOKEN,{metatoken:e})",
            1,
        ),
        (
            b"await(0,d.default)(c.default.SET_AUTH_TOKENTYPE,{tokentype:e})",
            b"void (0,d.default)(c.default.SET_AUTH_TOKENTYPE,{tokentype:e})",
            1,
        ),
        # Session initialization fans out across account state plus many
        # Windows-only hardware/social helpers. Preserve every initializer and
        # its failures, but prevent any missing OAF acknowledgement from
        # holding the entire authenticated session indefinitely.
        (
            b'const _=async e=>{try{await e()}catch(e){throw(0,i.logUnexpectedError)("Skyline",e),e}};',
            b"const _=e=>Promise.race([e(),new Promise(e=>setTimeout(e,5e3))]);                       ",
            1,
        ),
        # Wine's optional native focus helper expects a Windows HWND string but
        # receives Electron's integer pipe response. The callback URL has
        # already been sent at this point; skip only the cosmetic focus call so
        # the helper exits cleanly instead of reporting a false login failure.
        (
            b"A&&A.giveFocus(e.readUInt32LE(0))",
            b"0&&A.giveFocus(e.readUInt32LE(0))",
            1,
        ),
    )
    for old, new, expected in replacements:
        count = payload.count(old)
        if count != expected:
            raise RiftLiftError(
                f"pinned Meta client patch expected {expected} matching sites, found {count}"
            )
        payload = payload.replace(old, new)
    temporary = archive.with_suffix(".asar.riftlift.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, archive)
    marker.write_text("1\n")


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
        return target
    archive = download(
        PROTON_URL, paths.cache / f"{PROTON_VERSION}.tar.gz", PROTON_SHA256
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as source:
        source.extractall(target.parent, filter="data")
    if not (target / "proton").is_file():
        raise RiftLiftError("GE-Proton archive did not contain the expected launcher")
    return target


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


def install_meta_runtime(paths: Paths) -> Path:
    prefix = initialize_prefix(paths)
    support = prefix / "drive_c/Program Files/Oculus/Support"
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
                current_client_patch = (
                    package.name != "oculus-client"
                    or (destination / META_CLIENT_COMPAT_MARKER).is_file()
                )
                if current_package and current_client_patch:
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

    patch_meta_client(support / "oculus-client")

    patch_meta_runtime(support / "oculus-runtime")
    _install_meta_signing_root(paths, support)

    registration = support / ".riftlift-registry-v4"
    if not registration.is_file():
        base = r"C:\Program Files\Oculus"
        for key in (
            r"HKCU\Software\Oculus VR, LLC\Oculus",
            r"HKLM\Software\Oculus VR, LLC\Oculus",
            r"HKLM\Software\WOW6432Node\Oculus VR, LLC\Oculus",
        ):
            proton(
                paths,
                "runinprefix",
                "reg.exe",
                "add",
                key,
                "/v",
                "Base",
                "/t",
                "REG_SZ",
                "/d",
                base,
                "/f",
            )
            proton(
                paths,
                "runinprefix",
                "reg.exe",
                "add",
                key,
                "/v",
                "UseSystemProxy",
                "/t",
                "REG_DWORD",
                "/d",
                "0",
                "/f",
            )
        for key in (
            r"HKLM\Software\Oculus VR, LLC\Oculus",
            r"HKLM\Software\WOW6432Node\Oculus VR, LLC\Oculus",
        ):
            proton(
                paths,
                "runinprefix",
                "reg.exe",
                "add",
                key,
                "/v",
                "Gestalt",
                "/t",
                "REG_DWORD",
                "/d",
                "1",
                "/f",
            )
        for key in (
            r"HKCU\Software\Oculus VR, LLC\Oculus\Config",
            r"HKLM\Software\Oculus VR, LLC\Oculus\Config",
            r"HKLM\Software\WOW6432Node\Oculus VR, LLC\Oculus\Config",
        ):
            proton(
                paths,
                "runinprefix",
                "reg.exe",
                "add",
                key,
                "/v",
                "UseSystemProxy",
                "/t",
                "REG_DWORD",
                "/d",
                "0",
                "/f",
            )
        wow_config = r"HKLM\Software\WOW6432Node\Oculus VR, LLC\Oculus\Config"
        for name, value in (
            ("CldrLocaleCode", "en"),
            ("FbtLocaleCode", "en_US"),
            ("LanguageTag", "en-US"),
        ):
            proton(
                paths,
                "runinprefix",
                "reg.exe",
                "add",
                wow_config,
                "/v",
                name,
                "/t",
                "REG_SZ",
                "/d",
                value,
                "/f",
            )
        proton(
            paths,
            "runinprefix",
            "reg.exe",
            "add",
            wow_config,
            "/v",
            "HomeDemoMode",
            "/t",
            "REG_DWORD",
            "/d",
            "0",
            "/f",
        )
        protocol = (
            r'"C:\Program Files\Oculus\Support\oculus-client\Client.exe" -- --url "%1"'
        )
        proton(
            paths,
            "runinprefix",
            "reg.exe",
            "add",
            r"HKCU\Software\Classes\oculus\shell\open\command",
            "/ve",
            "/t",
            "REG_SZ",
            "/d",
            protocol,
            "/f",
        )
        service = r"HKLM\System\CurrentControlSet\Services\OVRService"
        values = (
            ("DisplayName", "REG_SZ", "Oculus VR Runtime Service"),
            ("Description", "REG_SZ", "Oculus VR Runtime Service"),
            (
                "ImagePath",
                "REG_EXPAND_SZ",
                r'"C:\Program Files\Oculus\Support\oculus-runtime\OVRServiceLauncher.exe"',
            ),
            ("ObjectName", "REG_SZ", "LocalSystem"),
            ("Type", "REG_DWORD", "16"),
            # RiftLift supplies the VR runtime.  Letting Wine auto-start Meta's
            # OVRService races the injected launcher on the first Proton run
            # and can leave the game stuck in umu.exe before its process exists.
            ("Start", "REG_DWORD", "4"),
            ("ErrorControl", "REG_DWORD", "1"),
        )
        for name, kind, value in values:
            proton(
                paths,
                "runinprefix",
                "reg.exe",
                "add",
                service,
                "/v",
                name,
                "/t",
                kind,
                "/d",
                value,
                "/f",
            )
        registration.write_text("1\n")
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
    target.write_text(
        json.dumps(
            {
                "version": 1,
                "runtime": [str(runtime)],
                "config": [str(config)],
                "log": [str(logs)],
            },
            indent=2,
        )
        + "\n"
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
    target.write_text(json.dumps(payload, indent=2) + "\n")
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
    openxr_runtime = active_runtime_json()
    paths.create()
    proton_root = install_proton(paths)
    install_meta_runtime(paths)
    install_rift_runtime(paths)
    install_openvr_runtime(paths)
    steamvr = steamvr_runtime_for_openxr(openxr_runtime)
    if steamvr is not None:
        _write_openvr_path_registry(paths, steamvr)
    install_platform_compat(paths)
    shutdown_compat_prefix(paths, proton_root)


def install_login_protocol_handler() -> Path:
    """Register Meta's oculus:// browser callback with the host desktop."""
    return install_protocol_handler()


def complete_login(paths: Paths, callback_url: str) -> int:
    """Hand a browser's oculus:// callback to RiftLift's active auth session."""
    return record_callback(paths, callback_url)


def login(paths: Paths) -> int:
    """Run the browser-backed sign-in flow for command-line users."""
    browser = default_browser()
    sign_out(paths)
    session = MetaAuthSession.begin(paths)
    process = launch_browser_login(paths, browser, session.login_url)
    print(f"Finish signing in to Meta in {browser.name}.")
    while process.poll() is None:
        if not session.callback_ready():
            time.sleep(1)
            continue
        complete_browser_login(paths, session)
        stop_browser(paths, browser, process)
        print("RiftLift is signed in to Meta.")
        return 0
    raise RiftLiftError("the browser closed before Meta sign-in finished")


def envision_profile() -> EnvisionProfile | None:
    """Read Envision's selection without requiring RiftLift to be its child."""
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    config_path = config_home / "envision/envision.json"
    try:
        payload = json.loads(config_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None

    uuid = payload.get("selected_profile_uuid")
    if not isinstance(uuid, str) or not uuid.strip():
        return None
    uuid = uuid.strip()
    selected: dict[object, object] = {}
    profiles = payload.get("user_profiles", [])
    if isinstance(profiles, list):
        selected = next(
            (
                profile
                for profile in profiles
                if isinstance(profile, dict) and profile.get("uuid") == uuid
            ),
            {},
        )

    configured_prefix = selected.get("prefix")
    prefixes = []
    if isinstance(configured_prefix, str) and configured_prefix.strip():
        prefixes.append(Path(configured_prefix).expanduser())
    # Envision's built-in UUIDs use a dash while their on-disk prefix names
    # historically used an underscore. Support both forms across releases.
    prefix_root = data_home / "envision/prefixes"
    prefixes.extend((prefix_root / uuid, prefix_root / uuid.replace("-", "_")))
    prefix = next((item for item in prefixes if item.is_dir()), prefixes[0])

    manifests = [prefix / "share/openxr/1/openxr_monado.json"]
    manifest = next((item for item in manifests if item.is_file()), manifests[0])
    raw_environment = selected.get("environment", {})
    environment = (
        {
            key: value
            for key, value in raw_environment.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        if isinstance(raw_environment, dict)
        else {}
    )
    library_dirs = [prefix / "lib", prefix / "lib64"]
    library_path = ":".join(str(item) for item in library_dirs if item.is_dir())
    if library_path and "LD_LIBRARY_PATH" not in environment:
        environment["LD_LIBRARY_PATH"] = library_path
    name = selected.get("name")
    return EnvisionProfile(
        uuid=uuid,
        name=name if isinstance(name, str) and name else uuid,
        prefix=prefix.resolve(),
        manifest=manifest.resolve(),
        environment=environment,
    )


def _runtime_manifest(candidate: Path, *, explicit: bool = False) -> Path | None:
    try:
        manifest = json.loads(candidate.read_text())
        runtime = manifest["runtime"]
        library = runtime["library_path"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        if explicit:
            raise RiftLiftError(
                f"XR_RUNTIME_JSON is not a valid OpenXR runtime manifest: {candidate}"
            ) from error
        return None
    if not isinstance(library, str) or not library:
        return None
    library_path = Path(library).expanduser()
    if library_path.is_absolute() or library_path.parent != Path("."):
        resolved_library = (
            library_path
            if library_path.is_absolute()
            else candidate.parent / library_path
        ).resolve()
        if not resolved_library.is_file():
            message = (
                f"OpenXR runtime manifest points to a missing library: "
                f"{resolved_library}"
            )
            if explicit:
                raise RiftLiftError(message)
            return None
    return candidate.resolve()


def running_steamvr_manifest() -> Path | None:
    """Return Valve's manifest only while this user has SteamVR running."""
    for comm in Path("/proc").glob("[0-9]*/comm"):
        try:
            if comm.stat().st_uid != os.getuid():
                continue
            if comm.read_text().strip() != "vrserver":
                continue
            executable = (comm.parent / "exe").resolve()
        except OSError:
            continue
        try:
            root = executable.parents[2]
        except IndexError:
            continue
        manifest = root / "steamxr_linux64.json"
        if (
            root.name == "SteamVR"
            and steamvr_runtime_for_openxr(manifest) == root.resolve()
        ):
            return manifest.resolve()
    return None


def installed_steamvr_manifest() -> Path | None:
    """Find a normal SteamVR install without requiring OpenXR registration."""
    try:
        from .steam_oculus import steam_library_roots

        libraries = steam_library_roots(steam_root())
    except Exception:
        return None
    for library in libraries:
        manifest = library / "steamapps/common/SteamVR/steamxr_linux64.json"
        if steamvr_runtime_for_openxr(manifest) is not None:
            return manifest.resolve()
    return None


def active_runtime_json() -> Path:
    explicit = os.environ.get("XR_RUNTIME_JSON")
    candidates = [Path(explicit)] if explicit else []
    if not explicit:
        running_steamvr = running_steamvr_manifest()
        if running_steamvr is not None:
            candidates.append(running_steamvr)
    envision = envision_profile()
    if not explicit and envision is not None:
        candidates.append(envision.manifest)
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    if envision is None:
        candidates.append(config_home / "openxr/1/active_runtime.json")

    data_dirs = [Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))]
    data_dirs.extend(
        Path(item)
        for item in os.environ.get(
            "XDG_DATA_DIRS", "/usr/local/share:/usr/share"
        ).split(":")
        if item
    )
    if envision is None:
        candidates.extend(
            directory / "openxr/1/openxr_monado.json" for directory in data_dirs
        )
        candidates.append(Path("/etc/openxr/1/active_runtime.json"))
        installed_steamvr = installed_steamvr_manifest()
        if installed_steamvr is not None:
            candidates.append(installed_steamvr)

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.expanduser()
        if candidate in seen:
            continue
        seen.add(candidate)
        if not candidate.is_file():
            continue
        resolved = _runtime_manifest(
            candidate,
            explicit=bool(explicit and candidate == Path(explicit).expanduser()),
        )
        if resolved is not None:
            return resolved
    if envision is not None:
        if not envision.manifest.is_file():
            raise RiftLiftError(
                f"Envision selected profile {envision.name!r} [{envision.uuid}], but "
                f"its Monado runtime is not built at {envision.manifest}; build that "
                "profile in Envision, select it, and retry"
            )
        try:
            payload = json.loads(envision.manifest.read_text())
            library_value = payload["runtime"]["library_path"]
            library = Path(library_value).expanduser()
            if not library.is_absolute():
                library = envision.manifest.parent / library
            detail = f"missing runtime library {library.resolve()}"
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            detail = f"invalid runtime manifest {envision.manifest}"
        raise RiftLiftError(
            f"Envision selected profile {envision.name!r} [{envision.uuid}], but it "
            f"has an unusable Monado build ({detail}); rebuild the profile in Envision"
        )
    raise RiftLiftError(
        "no usable OpenXR runtime was found; start an installed SteamVR session, "
        "build and select a Monado profile in Envision, or select a manifest with "
        "~/.config/openxr/1/active_runtime.json or XR_RUNTIME_JSON"
    )


def _envision_environment(runtime: Path) -> dict[str, str]:
    profile = envision_profile()
    if profile is None or profile.manifest != runtime.resolve():
        return {}
    return profile.environment.copy()


def _envision_version() -> str:
    """Read Envision's installed metadata without starting the application."""
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    directories = [
        data_home / "metainfo",
        data_home / "appdata",
        Path("/usr/local/share/metainfo"),
        Path("/usr/local/share/appdata"),
        Path("/usr/share/metainfo"),
        Path("/usr/share/appdata"),
    ]
    for root in (data_home / "flatpak/app", Path("/var/lib/flatpak/app")):
        try:
            directories.extend(root.glob("*nvision*/*/*/active/files/share/metainfo"))
        except OSError:
            pass
    for directory in directories:
        try:
            candidates = list(directory.glob("*nvision*.xml"))
        except OSError:
            continue
        for candidate in candidates:
            try:
                release = ET.parse(candidate).find(".//releases/release")
            except (OSError, ET.ParseError):
                continue
            version = release.get("version", "").strip() if release is not None else ""
            if version:
                return f"Envision {version}"
    return "not installed/unknown"


def xr_build_components() -> dict[str, str]:
    """Return external XR identities suitable for launch-time snapshots."""
    try:
        runtime = active_runtime_json()
        payload = json.loads(runtime.read_text())
        runtime_description = payload["runtime"]
        runtime_name = str(runtime_description.get("name", "unnamed"))
        library_value = runtime_description["library_path"]
        library = Path(library_value).expanduser()
        if not library.is_absolute() and library.parent != Path("."):
            library = (runtime.parent / library).resolve()
        profile = envision_profile()
        if (
            not library.is_absolute()
            and library.parent == Path(".")
            and profile is not None
            and profile.manifest == runtime
        ):
            library = next(
                (
                    candidate
                    for candidate in (
                        profile.prefix / "lib" / library,
                        profile.prefix / "lib64" / library,
                    )
                    if candidate.is_file()
                ),
                library,
            )
        manifest_hash = hashlib.sha256(runtime.read_bytes()).hexdigest()[:12]
        library_identity = library.name
        if library.is_file():
            library_identity += (
                f" sha256:{hashlib.sha256(library.read_bytes()).hexdigest()[:12]}"
            )
        result = {
            "openxr_manifest": f"{runtime} sha256:{manifest_hash}",
            "openxr_runtime": f"{runtime_name}: {library_identity}",
            "monado_runtime": (
                library_identity
                if "monado" in runtime_name.casefold()
                else f"not selected ({runtime_name})"
            ),
        }
    except Exception as error:
        result = {
            "openxr_manifest": f"unavailable: {error}",
            "openxr_runtime": "unavailable",
            "monado_runtime": "unavailable",
        }
    profile = envision_profile()
    result["envision_profile"] = (
        f"{profile.name} [{profile.uuid}]" if profile is not None else "not selected"
    )
    result["envision"] = _envision_version()
    return result


def platform_user_id(paths: Paths) -> str:
    """Return a unique identity that remains stable across game launches."""
    override = os.environ.get("RIFTLIFT_USER_ID", "").strip()
    identity_file = paths.config / "platform-user-id"

    def validate(value: str, source: str) -> str:
        try:
            parsed = int(value)
        except ValueError as error:
            raise RiftLiftError(f"{source} must contain a positive integer") from error
        if not 0 < parsed < 2**64:
            raise RiftLiftError(f"{source} must contain a positive 64-bit integer")
        return str(parsed)

    if override:
        return validate(override, "RIFTLIFT_USER_ID")
    if identity_file.is_file():
        return validate(identity_file.read_text().strip(), str(identity_file))

    paths.config.mkdir(parents=True, exist_ok=True)
    generated = str(secrets.randbits(62) | (1 << 61))
    try:
        descriptor = os.open(identity_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return validate(identity_file.read_text().strip(), str(identity_file))
    with os.fdopen(descriptor, "w") as stream:
        stream.write(f"{generated}\n")
    return generated


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
    for key, value in _envision_environment(runtime).items():
        if key == "LD_LIBRARY_PATH" and environment.get(key):
            values = [*value.split(":"), *environment[key].split(":")]
            environment[key] = ":".join(dict.fromkeys(item for item in values if item))
        else:
            environment.setdefault(key, value)
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
