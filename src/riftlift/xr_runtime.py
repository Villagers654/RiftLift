from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .config import Paths, xdg_config_home, xdg_data_dirs, xdg_data_home
from .util import RiftLiftError


@dataclass(frozen=True, slots=True)
class EnvisionProfile:
    """The Envision profile whose Monado build is selected by the user."""

    uuid: str
    name: str
    prefix: Path
    manifest: Path
    environment: dict[str, str]


def envision_profile() -> EnvisionProfile | None:
    """Read Envision's selection without requiring RiftLift to be its child."""
    config_home = xdg_config_home()
    data_home = xdg_data_home()
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

    manifest = prefix / "share/openxr/1/openxr_monado.json"
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


def active_runtime_json() -> Path:
    """Return the runtime override or the user's active OpenXR manifest."""
    explicit = os.environ.get("XR_RUNTIME_JSON", "").strip()
    if explicit:
        target = Path(explicit).expanduser()
        if not target.is_absolute() or not target.is_file():
            raise RiftLiftError(
                f"XR_RUNTIME_JSON does not name an existing absolute file: {target}"
            )
        return target.resolve()

    candidate = xdg_config_home() / "openxr/1/active_runtime.json"
    if candidate.is_file():
        return candidate.resolve()
    raise RiftLiftError(
        f"no active OpenXR runtime is configured at {candidate}; select one with "
        "your runtime manager or set XR_RUNTIME_JSON to its absolute manifest path"
    )


def _envision_version() -> str:
    """Read Envision's installed metadata without starting the application."""
    data_home = xdg_data_home()
    directories = [data_home / "metainfo", data_home / "appdata"]
    for root in xdg_data_dirs():
        directories.extend((root / "metainfo", root / "appdata"))
    for root in (data_home / "flatpak/app", Path("/var/lib/flatpak/app")):
        with contextlib.suppress(OSError):
            directories.extend(root.glob("*nvision*/*/*/active/files/share/metainfo"))
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
    except (
        RiftLiftError,
        OSError,
        json.JSONDecodeError,
        KeyError,
        AttributeError,
        TypeError,
    ) as error:
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
