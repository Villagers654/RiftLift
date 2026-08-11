from pathlib import Path
import zipfile
import tarfile
import io

from riftlift.config import Paths
from riftlift.runtime import (
    OPENVR_RUNTIME_VERSION,
    RUNTIME_VERSION,
    install_openvr_runtime,
    install_rift_runtime,
)

REQUIRED_RUNTIME_FILES = (
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


def _runtime_archive(path: Path, content: bytes) -> Path:
    archive = path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in REQUIRED_RUNTIME_FILES:
            bundle.writestr(name, content)
    return archive


def test_runtime_payload_is_reused_only_for_current_version(tmp_path, monkeypatch):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    archive = _runtime_archive(tmp_path, b"new")
    destination = paths.tools / "rift-runtime"
    for name in REQUIRED_RUNTIME_FILES:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"old")

    monkeypatch.setenv("RIFTLIFT_RUNTIME_ARCHIVE", str(archive))
    install_rift_runtime(paths)

    assert (destination / "RiftLiftLauncher.exe").read_bytes() == b"new"
    assert (destination / ".riftlift-version").read_text().strip() == RUNTIME_VERSION

    archive.unlink()
    assert install_rift_runtime(paths) == destination


def test_openvr_runtime_is_installed_and_versioned(tmp_path, monkeypatch):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    archive = tmp_path / "xrizer.tar.gz"
    payload = b"native openvr runtime"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("xrizer/libxrizer.so")
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    monkeypatch.setenv("RIFTLIFT_OPENVR_RUNTIME_ARCHIVE", str(archive))

    destination = install_openvr_runtime(paths)

    assert (destination / "libxrizer.so").read_bytes() == payload
    assert (destination / "bin/linux64/vrclient.so").read_bytes() == payload
    assert (
        destination / "bin/version.txt"
    ).read_text().strip() == OPENVR_RUNTIME_VERSION
    assert (
        destination / ".riftlift-version"
    ).read_text().strip() == OPENVR_RUNTIME_VERSION
    archive.unlink()
    assert install_openvr_runtime(paths) == destination
