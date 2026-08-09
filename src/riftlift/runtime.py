from __future__ import annotations

import hashlib
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
REVIVE_VERSION = "riftlift-0.1.1"
REVIVE_URL = "https://github.com/Villagers654/RiftLift/releases/download/v0.1.0/riftlift-compat.zip"
REVIVE_SHA256 = "ab28e900407da21e3f549dedd83b18ca486182e42f7a135014ce06c966c1f016"


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
            raise RiftLiftError(f"unsupported {name} build ({digest[:12]}); refusing an unsafe patch")
        for offset, before, after in recipe["changes"]:
            if payload[offset : offset + len(before)] != before:
                raise RiftLiftError(f"{name} compatibility patch did not match at {offset}")
            payload[offset : offset + len(before)] = after
        if hashlib.sha256(payload).hexdigest() != recipe["output"]:
            raise RiftLiftError(f"{name} compatibility patch produced an unexpected result")
        temporary = target.with_suffix(target.suffix + ".riftlift.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, target)

    # Monado owns headset discovery and display access. Meta's legacy Rift
    # hardware plug-ins recurse through Wine's synthetic SetupAPI devices until
    # OVRServer stack-overflows; keeping them loaded provides no functionality
    # to RiftLift. Preserve the files for inspection/recovery instead of deleting
    # them, while leaving OAF, IPC, and account services intact.
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
    marker = client / ".riftlift-client-compat-v10"
    if marker.is_file():
        return
    payload = archive.read_bytes()
    replacements = (
        (b'showBehavior:"whenSignaled"', b'showBehavior:"whenLoaded"  ', {0, 1, 2}),
        (b'show:!1,title:"Meta Horizon Link Client"', b'show:!0,title:"Meta Horizon Link Client"', {0, 1, 2}),
        # Undo the short-lived v3 development patch, if present. Keeping the
        # token store's normal promise semantics is essential after login.
        (
            b'this.promise=Promise.resolve("")            ',
            b"this.promise=new Promise(e=>this._resolve=e)",
            {0, 6},
        ),
        # Normalize the earlier bounded OAF-read patch, then make RiftLift's
        # private Electron profile the durable token source. OAF FastIPC never
        # returns /auth/gettoken under Wine, even after accepting token writes.
        (
            b'fetchAccessToken:async()=>Promise.race([(0,i.default)(r.default.GET_AUTH_TOKEN),new Promise(e=>setTimeout(e,5e3,""))]),invalidateAccessToken:async()=>0    ',
            b'fetchAccessToken:async()=>(0,i.default)(r.default.GET_AUTH_TOKEN),invalidateAccessToken:async()=>{throw new Error("invalidateAccessToken not implemented")}',
            {0, 3},
        ),
        (
            b'fetchAccessToken:async()=>(0,i.default)(r.default.GET_AUTH_TOKEN),invalidateAccessToken:async()=>{throw new Error("invalidateAccessToken not implemented")}',
            b'fetchAccessToken:async()=>localStorage.getItem("riftlift-token")||"",invalidateAccessToken:async()=>0                                                      ',
            {0, 3},
        ),
        # Undo the v5 experiment, if present, before applying the final locale
        # initialization below.
        (
            b't.initialize=()=>{h.set();r.default.fetchLanguageTag(f).then(e=>f(e,"never",!0),()=>0)}    ',
            b't.initialize=async()=>{const e=await r.default.fetchLanguageTag(f);f(e,"never",!0),h.set()}',
            {0, 2},
        ),
        # Proton has a valid built-in en-US locale, but OAF's language request
        # hangs for a fresh prefix. Initialize FBT with Meta's own compiled-in
        # locale before releasing readiness; this preserves every translation
        # invariant without depending on the broken IPC response.
        (
            b't.initialize=async()=>{const e=await r.default.fetchLanguageTag(f);f(e,"never",!0),h.set()}',
            b't.initialize=()=>{f(o.LANGUAGE_TAG,"never",!0),h.set()}                                    ',
            {0, 2},
        ),
        # OAF consumes these token writes but its Wine FastIPC server does not
        # acknowledge them. Do not deadlock the login state machine waiting for
        # acknowledgements; the calls still run and the primary access token is
        # also committed to the renderer's token store immediately afterward.
        (
            b"await(0,d.default)(c.default.SET_AUTH_TOKEN,{token:e}),r.default.set(e)",
            b"void (0,d.default)(c.default.SET_AUTH_TOKEN,{token:e}),r.default.set(e)",
            {0, 1},
        ),
        (
            b"t.setAccessToken=async(e,t)=>{void (0,d.default)(c.default.SET_AUTH_TOKEN,{token:e}),r.default.set(e),t.initialize&&await(0,p.initialize)(!0)};",
            b't.setAccessToken=async(e,t)=>{localStorage.setItem("riftlift-token",e),r.default.set(e),t.initialize&&await(0,p.initialize)(!0)};              ',
            {0, 1},
        ),
        (
            b"await(0,d.default)(c.default.SET_FRL_AUTH_TOKEN,{token:e})",
            b"void (0,d.default)(c.default.SET_FRL_AUTH_TOKEN,{token:e})",
            {0, 1},
        ),
        (
            b"await(0,d.default)(c.default.SET_AUTH_META_TOKEN,{metatoken:e})",
            b"void (0,d.default)(c.default.SET_AUTH_META_TOKEN,{metatoken:e})",
            {0, 1},
        ),
        (
            b"await(0,d.default)(c.default.SET_AUTH_TOKENTYPE,{tokentype:e})",
            b"void (0,d.default)(c.default.SET_AUTH_TOKENTYPE,{tokentype:e})",
            {0, 1},
        ),
        # Session initialization fans out across account state plus many
        # Windows-only hardware/social helpers. Preserve every initializer and
        # its failures, but prevent any missing OAF acknowledgement from
        # holding the entire authenticated session indefinitely.
        (
            b'const _=async e=>{try{await e()}catch(e){throw(0,i.logUnexpectedError)("Skyline",e),e}};',
            b"const _=e=>Promise.race([e(),new Promise(e=>setTimeout(e,5e3))]);                       ",
            {0, 1},
        ),
        # Wine's optional native focus helper expects a Windows HWND string but
        # receives Electron's integer pipe response. The callback URL has
        # already been sent at this point; skip only the cosmetic focus call so
        # the helper exits cleanly instead of reporting a false login failure.
        (
            b"A&&A.giveFocus(e.readUInt32LE(0))",
            b"0&&A.giveFocus(e.readUInt32LE(0))",
            {1},
        ),
    )
    for old, new, expected in replacements:
        count = payload.count(old)
        if count not in expected:
            raise RiftLiftError(f"Meta client visibility patch found an unexpected {count} sites")
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
    # A host integration may export xrizer's OpenVR override session-wide.
    # RiftLift uses ReviveXR/WineOpenXR directly; injecting xrizer into Meta's
    # client, reg.exe, or prefix bootstrap is both unnecessary and harmful.
    for variable in (
        "VR_OVERRIDE",
        "XR_RUNTIME_JSON",
        "PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES",
        "OXR_ZERO_TIME_IS_NOW",
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
    prefix = paths.prefix / "pfx"
    # OAF treats this legacy Known Folder as mandatory even though RiftLift
    # never creates a Windows shortcut there. Proton does not create it.
    (prefix / "drive_c/users/steamuser/AppData/Roaming/Microsoft/Internet Explorer/Quick Launch").mkdir(
        parents=True, exist_ok=True
    )
    return prefix


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

    patch_meta_client(support / "oculus-client")

    patch_meta_runtime(support / "oculus-runtime")

    registration = support / ".riftlift-registry-v3"
    if not registration.is_file():
        base = r"C:\Program Files\Oculus"
        for key in (
            r"HKCU\Software\Oculus VR, LLC\Oculus",
            r"HKLM\Software\Oculus VR, LLC\Oculus",
            r"HKLM\Software\WOW6432Node\Oculus VR, LLC\Oculus",
        ):
            proton(paths, "runinprefix", "reg.exe", "add", key, "/v", "Base", "/t", "REG_SZ", "/d", base, "/f")
            proton(paths, "runinprefix", "reg.exe", "add", key, "/v", "UseSystemProxy", "/t", "REG_DWORD", "/d", "0", "/f")
        for key in (
            r"HKLM\Software\Oculus VR, LLC\Oculus",
            r"HKLM\Software\WOW6432Node\Oculus VR, LLC\Oculus",
        ):
            proton(paths, "runinprefix", "reg.exe", "add", key, "/v", "Gestalt", "/t", "REG_DWORD", "/d", "1", "/f")
        for key in (
            r"HKCU\Software\Oculus VR, LLC\Oculus\Config",
            r"HKLM\Software\Oculus VR, LLC\Oculus\Config",
            r"HKLM\Software\WOW6432Node\Oculus VR, LLC\Oculus\Config",
        ):
            proton(paths, "runinprefix", "reg.exe", "add", key, "/v", "UseSystemProxy", "/t", "REG_DWORD", "/d", "0", "/f")
        wow_config = r"HKLM\Software\WOW6432Node\Oculus VR, LLC\Oculus\Config"
        for name, value in (("CldrLocaleCode", "en"), ("FbtLocaleCode", "en_US"), ("LanguageTag", "en-US")):
            proton(paths, "runinprefix", "reg.exe", "add", wow_config, "/v", name, "/t", "REG_SZ", "/d", value, "/f")
        proton(paths, "runinprefix", "reg.exe", "add", wow_config, "/v", "HomeDemoMode", "/t", "REG_DWORD", "/d", "0", "/f")
        protocol = r'"C:\Program Files\Oculus\Support\oculus-client\Client.exe" -- --url "%1"'
        proton(paths, "runinprefix", "reg.exe", "add", r"HKCU\Software\Classes\oculus\shell\open\command", "/ve", "/t", "REG_SZ", "/d", protocol, "/f")
        service = r"HKLM\System\CurrentControlSet\Services\OVRService"
        values = (
            ("DisplayName", "REG_SZ", "Oculus VR Runtime Service"),
            ("Description", "REG_SZ", "Oculus VR Runtime Service"),
            ("ImagePath", "REG_EXPAND_SZ", r'"C:\Program Files\Oculus\Support\oculus-runtime\OVRServiceLauncher.exe"'),
            ("ObjectName", "REG_SZ", "LocalSystem"),
            ("Type", "REG_DWORD", "16"),
            ("Start", "REG_DWORD", "2"),
            ("ErrorControl", "REG_DWORD", "1"),
        )
        for name, kind, value in values:
            proton(paths, "runinprefix", "reg.exe", "add", service, "/v", name, "/t", kind, "/d", value, "/f")
        registration.write_text("1\n")
    return support


def install_revive(paths: Paths) -> Path:
    destination = paths.tools / "revive"
    required = ("ReviveInjector.exe", "LibReviveXR64.dll", "openvr_api64.dll", "LibOVRPlatformImpl64_1.dll")
    if all((destination / name).is_file() for name in required):
        return destination
    override = os.environ.get("RIFTLIFT_REVIVE_ARCHIVE")
    archive = (
        Path(override).expanduser()
        if override
        else download(REVIVE_URL, paths.cache / f"riftlift-compat-{REVIVE_VERSION}.zip", REVIVE_SHA256)
    )
    if destination.exists():
        shutil.rmtree(destination)
    _safe_zip(archive, destination)
    nested = destination / "riftlift-revive"
    if nested.is_dir() and not (destination / "ReviveInjector.exe").exists():
        for item in nested.iterdir():
            shutil.move(str(item), destination / item.name)
        nested.rmdir()
    if not all((destination / name).is_file() for name in required):
        raise RiftLiftError("Revive payload is incomplete")
    return destination


def install_platform_compat(paths: Paths) -> Path:
    # The legacy PC Platform SDK DLLs live in the main runtime package. The
    # similarly named oculus-platform-runtime package is a newer service and
    # does not contain the link/import DLLs used by Rift games.
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
    bundled = paths.tools / "revive" / "LibOVRPlatformImpl64_1.dll"
    shim = Path(override).expanduser() if override else bundled
    if not shim.is_file():
        raise RiftLiftError("RiftLift platform compatibility DLL is missing from the release payload")
    shutil.copy2(shim, destination / "LibOVRPlatformImpl64_1.dll")
    shutil.copy2(shim, runtime_impl)
    return destination


def setup(paths: Paths) -> None:
    # Fail before downloading gigabytes if the host is not actually ready to
    # run OpenXR applications. RiftLift consumes an existing runtime; headset
    # drivers and compositor lifecycle remain the host setup's responsibility.
    active_runtime_json()
    paths.create()
    install_proton(paths)
    install_meta_runtime(paths)
    install_revive(paths)
    install_platform_compat(paths)
    install_login_protocol_handler()


def install_login_protocol_handler() -> Path:
    """Register Meta's oculus:// browser callback with the host desktop."""
    applications = Path.home() / ".local/share/applications"
    applications.mkdir(parents=True, exist_ok=True)
    desktop = applications / "riftlift-meta-login.desktop"
    executable = Path.home() / ".local/bin/riftlift"
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=RiftLift Meta Login\n"
        "NoDisplay=true\n"
        f"Exec={executable} callback %u\n"
        "MimeType=x-scheme-handler/oculus;x-scheme-handler/oculus-client;\n"
    )
    desktop.chmod(0o755)
    if update_database := shutil.which("update-desktop-database"):
        run((update_database, str(applications)))
    if xdg_mime := shutil.which("xdg-mime"):
        run((xdg_mime, "default", desktop.name, "x-scheme-handler/oculus"))
        run((xdg_mime, "default", desktop.name, "x-scheme-handler/oculus-client"))
    return desktop


def complete_login(paths: Paths, callback_url: str) -> int:
    """Forward a browser's oculus:// callback into the persistent prefix."""
    if not callback_url.startswith(("oculus://", "oculus-client://")):
        raise RiftLiftError("Meta login callback must use the oculus:// scheme")
    support = install_meta_runtime(paths)
    client = support / "oculus-client/Client.exe"
    # The browser callback arrives while Client.exe is already running. Proton's
    # regular `run` path serializes through its Steam launch wrapper and can
    # deadlock behind that process; runinprefix starts the Electron singleton
    # directly, which forwards the URL to the existing client and exits.
    # Use the equals form so Proton cannot consume Electron's option/value
    # separator while forwarding the custom-scheme URL.
    return proton(paths, "runinprefix", str(client), f"--url={callback_url}").returncode


def login(paths: Paths) -> int:
    support = install_meta_runtime(paths)
    client = support / "oculus-client/Client.exe"
    print("Sign in in the Meta Horizon Link window. RiftLift keeps this shared prefix for future games.")
    arguments = ["run", str(client)]
    if debug_port := os.environ.get("RIFTLIFT_CLIENT_DEBUG_PORT"):
        arguments.append(f"--remote-debugging-port={int(debug_port)}")
    return proton(paths, *arguments).returncode


def active_runtime_json() -> Path:
    explicit = os.environ.get("XR_RUNTIME_JSON")
    candidates = [Path(explicit)] if explicit else []
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    candidates.append(config_home / "openxr/1/active_runtime.json")

    # A correctly configured loader normally exposes active_runtime.json. The
    # named Monado manifests are fallbacks for distributions that install the
    # runtime but omit the user-level selector.
    data_dirs = [Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))]
    data_dirs.extend(Path(item) for item in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if item)
    candidates.extend(directory / "openxr/1/openxr_monado.json" for directory in data_dirs)
    candidates.append(Path("/etc/openxr/1/active_runtime.json"))

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.expanduser()
        if candidate in seen:
            continue
        seen.add(candidate)
        if not candidate.is_file():
            continue
        try:
            manifest = json.loads(candidate.read_text())
            library = manifest["runtime"]["library_path"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            if explicit and candidate == Path(explicit).expanduser():
                raise RiftLiftError(f"XR_RUNTIME_JSON is not a valid OpenXR runtime manifest: {candidate}") from error
            continue
        if not isinstance(library, str) or not library:
            continue
        return candidate.resolve()
    raise RiftLiftError(
        "no active OpenXR runtime was found; select your working Monado manifest with "
        "~/.config/openxr/1/active_runtime.json or XR_RUNTIME_JSON"
    )


def launch_environment(paths: Paths, game_dir: Path, platform_shim: bool, platform_offline: bool = False) -> dict[str, str]:
    environment = proton_environment(paths, game_dir)
    runtime = active_runtime_json()
    existing_overrides = environment.get("WINEDLLOVERRIDES", "").strip(";")
    environment.update(
        {
            "XR_RUNTIME_JSON": str(runtime),
            "PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES": "1",
            "OXR_ZERO_TIME_IS_NOW": "1",
            "RIFTLIFT_XRIZER": "1",
            # Proton's runinprefix verb does not apply its normal per-game DXVK
            # override. WineD3D lacks IDXGIVkInteropDevice, so WineOpenXR rejects
            # Revive's D3D11 graphics binding before input can be attached.
            "WINEDLLOVERRIDES": f"d3d11=n;dxgi=n{';' + existing_overrides if existing_overrides else ''}",
        }
    )
    if platform_shim:
        compatibility = install_platform_compat(paths)
        compatibility_win = linux_to_windows(compatibility)
        meta_runtime = paths.prefix / "pfx/drive_c/Program Files/Oculus/Support/oculus-runtime"
        meta_runtime_win = linux_to_windows(meta_runtime)
        # Do not set LIBOVR_DLL_DIR here. OVRPlugin uses that variable for the
        # VR runtime as well as the Platform SDK; pointing it at our platform
        # shim makes it reject LibOVRRT before ReviveXR can intercept the load.
        # Put the compatibility directory first so the Platform SDK still uses
        # our shim, followed by Meta's signed runtime loader. Newer OVRPlugin
        # builds verify that signed LibOVRRT file before calling LoadLibrary;
        # ReviveXR intercepts that final load and substitutes itself.
        environment["WINEPATH"] = f"{compatibility_win};{meta_runtime_win}"
        if platform_offline:
            environment["RIFTLIFT_PLATFORM_OFFLINE"] = "1"
    return environment
