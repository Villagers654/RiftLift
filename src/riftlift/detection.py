from __future__ import annotations

import os
import struct
from pathlib import Path

_OCULUS_FILENAMES = {
    "libovrrt64_1.dll",
    "oculusxrplugin.dll",
    "ovrplugin.dll",
}
_OCULUS_IMPORTS = (b"libovrrt64_1.dll",)
_D3D12_IMPORTS = (b"d3d12.dll", "d3d12.dll".encode("utf-16le"))
_NON_GAME_EXECUTABLES = (
    "crash",
    "helper",
    "installer",
    "prereq",
    "redist",
    "report",
    "setup",
    "unins",
    "uninstall",
)


def is_pe64(path: Path) -> bool:
    """Return whether *path* is a 64-bit Windows PE executable or DLL."""
    try:
        with path.open("rb") as stream:
            header = stream.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                return False
            pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
            stream.seek(pe_offset)
            return stream.read(6) == b"PE\0\0\x64\x86"
    except (OSError, struct.error):
        return False


def _contains_any(path: Path, needles: tuple[bytes, ...]) -> bool:
    overlap = max(map(len, needles), default=1) - 1
    tail = b""
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                payload = (tail + chunk).lower()
                if any(needle in payload for needle in needles):
                    return True
                tail = payload[-overlap:] if overlap else b""
    except OSError:
        return False
    return False


def _walk(directory: Path):
    for root, directories, files in os.walk(directory):
        directories[:] = [name for name in directories if not name.startswith(".")]
        base = Path(root)
        for name in files:
            yield base / name


def uses_oculus_runtime(directory: Path) -> bool:
    """Detect a 64-bit Oculus PC runtime without relying on an engine layout."""
    executables: list[Path] = []
    for path in _walk(directory):
        lowered = path.name.casefold()
        if lowered.endswith(".exe") and is_pe64(path):
            executables.append(path)
        if lowered not in _OCULUS_FILENAMES:
            continue
        components = {part.casefold() for part in path.parts}
        if lowered == "libovrrt64_1.dll" or components & {"win64", "x86_64"}:
            return True
        if is_pe64(path):
            return True
    return any(_contains_any(path, _OCULUS_IMPORTS) for path in executables)


def uses_openvr_runtime(directory: Path) -> bool:
    """Return whether the install also carries an OpenVR client runtime."""
    return any(path.name.casefold() == "openvr_api.dll" for path in _walk(directory))


def uses_d3d12_runtime(executable: Path) -> bool:
    """Return whether the selected Windows game executable references D3D12.

    ReviveXR's direct WineOpenXR path currently supports D3D11 clients.  A
    static import/string probe lets D3D12 Oculus clients select classic Revive
    before launch, without maintaining a title database or deliberately
    failing the first run.
    """
    return _contains_any(executable, _D3D12_IMPORTS)


def _unity_executables(directory: Path) -> list[Path]:
    result: list[Path] = []
    for data in directory.glob("*_Data"):
        executable = directory / f"{data.name.removesuffix('_Data')}.exe"
        if is_pe64(executable):
            result.append(executable)
    return result


def _unreal_executables(directory: Path) -> list[Path]:
    return [
        path
        for path in directory.glob("*/Binaries/Win64/*-Win64-Shipping.exe")
        if is_pe64(path)
    ]


def is_unreal_shipping(path: Path) -> bool:
    parts = [part.casefold() for part in path.parts]
    return (
        path.name.casefold().endswith("-win64-shipping.exe")
        and len(parts) >= 3
        and parts[-3:-1] == ["binaries", "win64"]
    )


def best_windows_executable(directory: Path, preferred: Path | None = None) -> Path:
    """Choose the actual 64-bit game process, avoiding engine bootstrappers."""
    directory = directory.resolve()
    preferred_path = (directory / preferred).resolve() if preferred else None
    unreal = _unreal_executables(directory)
    if unreal:
        if preferred_path:
            stem = preferred_path.stem.casefold()
            matching = [
                path for path in unreal if path.name.casefold().startswith(stem)
            ]
            if matching:
                return matching[0]
        return sorted(unreal)[0]

    unity = _unity_executables(directory)
    if preferred_path in unity:
        return preferred_path
    if unity:
        return sorted(unity)[0]
    if preferred_path and is_pe64(preferred_path):
        return preferred_path

    candidates = [
        path
        for path in _walk(directory)
        if path.suffix.casefold() == ".exe"
        and is_pe64(path)
        and not any(marker in path.name.casefold() for marker in _NON_GAME_EXECUTABLES)
    ]
    if not candidates:
        requested = preferred.as_posix() if preferred else "<auto-detect>"
        raise ValueError(
            f"no 64-bit game executable was found (preferred: {requested})"
        )
    candidates.sort(
        key=lambda path: (len(path.relative_to(directory).parts), str(path))
    )
    return candidates[0]
