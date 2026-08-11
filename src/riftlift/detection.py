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


def _pe_imported_dlls(path: Path) -> set[str] | None:
    """Read normal and delay-load DLL imports from a PE image.

    Return ``None`` for malformed/non-PE input so callers can retain a safe
    string-probe fallback for clients that load their graphics API dynamically.
    """

    try:
        with path.open("rb") as stream:
            dos = stream.read(64)
            if len(dos) < 64 or dos[:2] != b"MZ":
                return None
            pe_offset = struct.unpack_from("<I", dos, 0x3C)[0]
            stream.seek(pe_offset)
            if stream.read(4) != b"PE\0\0":
                return None
            coff = stream.read(20)
            if len(coff) != 20:
                return None
            section_count = struct.unpack_from("<H", coff, 2)[0]
            optional_size = struct.unpack_from("<H", coff, 16)[0]
            optional = stream.read(optional_size)
            if len(optional) != optional_size or len(optional) < 120:
                return None
            magic = struct.unpack_from("<H", optional)[0]
            if magic == 0x20B:
                directories_offset = 112
                image_base = struct.unpack_from("<Q", optional, 24)[0]
            elif magic == 0x10B:
                directories_offset = 96
                image_base = struct.unpack_from("<I", optional, 28)[0]
            else:
                return None

            def directory(index: int) -> tuple[int, int]:
                offset = directories_offset + index * 8
                if offset + 8 > len(optional):
                    return 0, 0
                return struct.unpack_from("<II", optional, offset)

            import_directory = directory(1)
            delay_directory = directory(13)
            sections = []
            for _ in range(section_count):
                section = stream.read(40)
                if len(section) != 40:
                    return None
                virtual_size, virtual_address, raw_size, raw_offset = (
                    struct.unpack_from("<IIII", section, 8)
                )
                sections.append(
                    (virtual_address, max(virtual_size, raw_size), raw_offset, raw_size)
                )

            def file_offset(rva: int) -> int | None:
                for virtual_address, span, raw_offset, raw_size in sections:
                    delta = rva - virtual_address
                    if 0 <= delta < span and delta < raw_size:
                        return raw_offset + delta
                return None

            def dll_name(rva: int) -> str | None:
                offset = file_offset(rva)
                if offset is None:
                    return None
                stream.seek(offset)
                payload = stream.read(512).split(b"\0", 1)[0]
                try:
                    return payload.decode("ascii").casefold()
                except UnicodeDecodeError:
                    return None

            result: set[str] = set()

            import_rva, import_size = import_directory
            import_offset = file_offset(import_rva)
            if import_offset is not None:
                stream.seek(import_offset)
                for _ in range(min(import_size // 20 + 1, 4096)):
                    descriptor = stream.read(20)
                    if len(descriptor) != 20 or descriptor == bytes(20):
                        break
                    name = dll_name(struct.unpack_from("<I", descriptor, 12)[0])
                    if name:
                        result.add(name)
                    stream.seek(import_offset + 20 * (_ + 1))

            delay_rva, delay_size = delay_directory
            delay_offset = file_offset(delay_rva)
            if delay_offset is not None:
                stream.seek(delay_offset)
                for _ in range(min(delay_size // 32 + 1, 4096)):
                    descriptor = stream.read(32)
                    if len(descriptor) != 32 or descriptor == bytes(32):
                        break
                    attributes, name_address = struct.unpack_from("<II", descriptor)
                    name_rva = (
                        name_address if attributes & 1 else name_address - image_base
                    )
                    name = dll_name(name_rva) if name_rva >= 0 else None
                    if name:
                        result.add(name)
                    stream.seek(delay_offset + 32 * (_ + 1))
            return result
    except (OSError, struct.error, ValueError):
        return None


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


def uses_oculus_xr_plugin(directory: Path) -> bool:
    """Return whether the install packages Unity's Oculus XR provider.

    This provider loads the native Oculus SDK through a Unity subsystem and
    performs graphics initialization before the game reaches its render loop.
    Treating that integration as an installed capability lets the launcher
    select the mature compositor-backed translation path without a title list.
    """
    return any(
        path.name.casefold() == "oculusxrplugin.dll" for path in _walk(directory)
    )


def uses_d3d12_runtime(executable: Path) -> bool:
    """Return whether the selected Windows game executable references D3D12.

    The direct WineOpenXR bridge currently supports D3D11 clients. A static
    import/string probe lets D3D12 Oculus clients select the OpenVR bridge
    before launch, without maintaining a title database or deliberately
    failing the first run.
    """
    imported = _pe_imported_dlls(executable)
    if imported:
        if "d3d12.dll" in imported and "d3d11.dll" not in imported:
            return True
        # Multi-renderer engines commonly import both APIs but default to D3D11
        # unless explicitly launched with a D3D12 flag. A real D3D11 import is
        # stronger evidence than either an arbitrary string or an unused D3D12
        # renderer compiled into the same executable.
        if "d3d11.dll" in imported:
            return False
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


def is_unity_player(path: Path) -> bool:
    """Return whether *path* is the player executable of a Unity install."""
    return (path.parent / f"{path.stem}_Data").is_dir()


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
