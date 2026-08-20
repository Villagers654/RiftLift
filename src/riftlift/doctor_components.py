"""Installed-component inventory and launch-snapshot comparison."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import __version__
from .config import Paths
from .diagnostics import system_build_components
from .runtime import (
    DXVK_SHA256,
    DXVK_VERSION,
    META_PACKAGES,
    META_VERSION,
    OPENVR_RUNTIME_VERSION,
    PROTON_VERSION,
    RUNTIME_VERSION,
    proton_dir,
    steamvr_runtime_for_openxr,
)
from .util import RiftLiftError
from .xr_runtime import active_runtime_json, xr_build_components


def file_identity(path: Path) -> str:
    if not path.is_file():
        return "missing"
    try:
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
        digest = hasher.hexdigest()[:12]
        return f"present, {path.stat().st_size // 1024} KiB, sha256 {digest}"
    except OSError as error:
        return f"unreadable: {error}"


def proton_version(path: Path) -> str:
    for candidate in (path / "version", path / "files/version"):
        try:
            value = candidate.read_text(errors="replace").strip()
        except OSError:
            continue
        if value:
            return value[:160]
    return path.name


def installed_marker(path: Path) -> str:
    try:
        value = (path / ".riftlift-version").read_text(errors="replace").strip()
    except OSError:
        return "missing"
    return value[:160] or "unknown"


def installed_dxvk(path: Path) -> tuple[bool, str]:
    marker = path / "files/lib/wine/dxvk/.riftlift-dxvk.json"
    try:
        payload = json.loads(marker.read_text())
        version = str(payload.get("version", ""))
        expected_files = payload.get("files", {})
        if not isinstance(expected_files, dict) or not expected_files:
            raise ValueError("file manifest is empty")
        damaged = []
        for relative, expected in expected_files.items():
            target = marker.parent / str(relative)
            if (
                not target.is_file()
                or hashlib.sha256(target.read_bytes()).hexdigest() != expected
            ):
                damaged.append(str(relative))
        if damaged:
            return (
                False,
                f"{version or 'unknown'}; missing or changed: {', '.join(damaged)}",
            )
        artifact = str(payload.get("artifact_sha256", ""))[:12]
        return (
            version == DXVK_VERSION and artifact == DXVK_SHA256[:12],
            f"{version} sha256:{artifact or 'unknown'}",
        )
    except (OSError, json.JSONDecodeError, AttributeError, ValueError) as error:
        return False, f"missing or unreadable: {error}"


def current_components(paths: Paths) -> dict[str, str]:
    try:
        proton = proton_dir()
        proton_build = (
            proton_version(proton) if (proton / "proton").is_file() else "missing"
        )
    except RiftLiftError:
        proton = None
        proton_build = "missing"
    if proton is None:
        dxvk_build = "missing"
    else:
        dxvk_ok, dxvk_detail = installed_dxvk(proton)
        dxvk_build = dxvk_detail if dxvk_ok else f"invalid ({dxvk_detail})"
    runtime_build = installed_marker(paths.tools / "rift-runtime")
    support = paths.prefix / "pfx/drive_c/Program Files/Oculus/Support"
    meta_builds: dict[str, str] = {}
    for package in META_PACKAGES:
        marker = support / package.name / ".riftlift-package.json"
        try:
            package_sha256 = str(json.loads(marker.read_text()).get("sha256", ""))
        except (OSError, json.JSONDecodeError):
            package_sha256 = ""
        meta_builds[f"meta_{package.name.replace('-', '_')}"] = (
            f"{META_VERSION} sha256:{package_sha256[:12]}"
            if package_sha256
            else "missing/unknown"
        )
    bundled_xrizer = installed_marker(paths.tools / "openvr-runtime")
    selected_openvr = bundled_xrizer
    openvr_transport = f"XRizer {bundled_xrizer} -> active OpenXR runtime"
    try:
        steamvr = steamvr_runtime_for_openxr(active_runtime_json())
    except RiftLiftError:
        steamvr = None
    if steamvr is not None:
        try:
            steamvr_build = (steamvr / "bin/version.txt").read_text().strip()
        except OSError:
            steamvr_build = "unknown"
        selected_openvr = f"SteamVR {steamvr_build[:160]}"
        openvr_transport = "SteamVR direct (no XRizer)"
    return {
        "riftlift": __version__,
        "compat_runtime": runtime_build,
        "bundled_xrizer": bundled_xrizer,
        "openvr_runtime": selected_openvr,
        "openvr_transport": openvr_transport,
        "proton": proton_build,
        "dxvk": dxvk_build,
        **meta_builds,
        "platform_bridge": f"compat-runtime:{runtime_build}",
        **system_build_components(probe_vulkan=False),
        **xr_build_components(),
    }


def expected_components() -> dict[str, str]:
    return {
        "riftlift": __version__,
        "compat_runtime": RUNTIME_VERSION,
        "bundled_xrizer": OPENVR_RUNTIME_VERSION,
        "proton": PROTON_VERSION,
        "dxvk": f"{DXVK_VERSION} sha256:{DXVK_SHA256[:12]}",
        **{
            f"meta_{package.name.replace('-', '_')}": (
                f"{META_VERSION} sha256:{package.sha256[:12]}"
            )
            for package in META_PACKAGES
        },
        "platform_bridge": f"compat-runtime:{RUNTIME_VERSION}",
    }


def component_matches(name: str, installed: str, expected: str) -> bool:
    if name == "proton":
        return installed == expected or installed.endswith(f" {expected}")
    return installed == expected


def component_comparison(
    launches: list[dict[str, object]], current: dict[str, str]
) -> list[str]:
    if not launches:
        return ["No launch snapshot is available for comparison."]
    newest = launches[0]
    captured = newest.get("components")
    if not isinstance(captured, dict):
        legacy = newest.get("riftlift_version", "unknown")
        return [
            "LEGACY/UNKNOWN: this launch predates the complete component snapshot "
            f"(captured RiftLift={legacy}; doctor RiftLift={__version__}). Reproduce "
            "with the current build for a reliable comparison."
        ]
    lines: list[str] = []
    names = list(expected_components())
    names.extend(
        sorted(name for name in set(captured) | set(current) if name not in names)
    )
    for name in names:
        before = str(captured.get(name, "unknown"))
        now = current.get(name, "unknown")
        if before.startswith("not-used("):
            state = "NOT USED"
        else:
            state = "SAME" if before == now else "CHANGED"
        lines.append(f"{state:7} {name}: launch={before}; doctor={now}")
    return lines
