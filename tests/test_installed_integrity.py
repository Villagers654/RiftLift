import hashlib
import io
import json
import tarfile
import zipfile

import pytest

from riftlift import runtime
from riftlift.config import Paths
from riftlift.doctor_components import current_components


@pytest.fixture
def paths(tmp_path):
    return Paths(
        *(tmp_path / n for n in ("data", "cache", "config", "games", "prefix", "tools"))
    )


@pytest.mark.parametrize("damage", ["missing", "corrupt", "legacy", "list-marker"])
@pytest.mark.parametrize("name", ["RiftLiftOpenXR64.dll", "LibOVRPlatformImpl64_1.dll"])
def test_compat_payload_damage_is_reported_and_repaired(
    paths, tmp_path, monkeypatch, damage, name
):
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for member in runtime.RIFT_RUNTIME_FILES:
            bundle.writestr(member, b"original")
    monkeypatch.setenv("RIFTLIFT_RUNTIME_ARCHIVE", str(archive))
    destination = runtime.install_rift_runtime(paths)
    assert current_components(paths)["compat_runtime"] == runtime.RUNTIME_VERSION
    target = destination / name
    if damage == "missing":
        target.unlink()
    elif damage == "corrupt":
        target.write_bytes(b"changed")
    elif damage == "legacy":
        (destination / ".riftlift-files.json").unlink()
    else:
        (destination / ".riftlift-files.json").write_text("[]")
    assert current_components(paths)["compat_runtime"].startswith("invalid")
    runtime.install_rift_runtime(paths)
    assert target.read_bytes() == b"original"
    assert current_components(paths)["compat_runtime"] == runtime.RUNTIME_VERSION


@pytest.mark.parametrize(
    "damage", ["missing", "corrupt", "list-marker", "null-marker", "legacy"]
)
@pytest.mark.parametrize("signed", [False, True])
def test_meta_damage_is_reported_and_repaired(
    paths, tmp_path, monkeypatch, damage, signed
):
    package = runtime.MetaPackage("test", "id", "archive-hash", ("test.dll",), signed)
    monkeypatch.setattr(runtime, "META_PACKAGES", (package,))
    monkeypatch.setattr("riftlift.doctor_components.META_PACKAGES", (package,))
    monkeypatch.setattr(
        runtime,
        "META_RUNTIME_SIGNED_FILES",
        {"test.dll": hashlib.sha256(b"original").hexdigest()},
    )
    archive = tmp_path / "meta.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("test.dll", b"original")
    monkeypatch.setattr(runtime, "download", lambda *_: archive)
    support = paths.prefix / "pfx/drive_c/Program Files/Oculus/Support"
    runtime._install_meta_packages(paths, support)
    target = support / "test/test.dll"
    marker = support / "test/.riftlift-package.json"
    if damage == "missing":
        target.unlink()
    elif damage == "corrupt":
        target.write_bytes(b"changed")
    elif damage == "legacy":
        marker.write_text(json.dumps({"sha256": package.sha256}))
    else:
        marker.write_text("[]" if damage == "list-marker" else "null")
    expected_current = signed and damage == "legacy"
    assert runtime.meta_package_current(target.parent, package) == expected_current
    if not expected_current:
        assert current_components(paths)["meta_test"] == "missing/unknown"
    runtime._install_meta_packages(paths, support)
    assert target.read_bytes() == b"original"
    assert runtime.meta_package_current(target.parent, package)
    assert current_components(paths)["meta_test"].startswith(runtime.META_VERSION)


@pytest.mark.parametrize("member", runtime.OPENVR_RUNTIME_FILES)
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_openvr_payload_damage_is_reported_and_repaired(
    paths, tmp_path, monkeypatch, member, damage
):
    # Exercise installer and hashing; native loading is covered by the real loader tests.
    monkeypatch.setattr(runtime, "validate_openvr_library", lambda _: None)
    archive = tmp_path / "xrizer.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("xrizer/libxrizer.so")
        info.size = len(b"original")
        bundle.addfile(info, io.BytesIO(b"original"))
    monkeypatch.setenv("RIFTLIFT_OPENVR_RUNTIME_ARCHIVE", str(archive))
    destination = runtime.install_openvr_runtime(paths)
    target = destination / member
    original = target.read_bytes()
    if damage == "missing":
        target.unlink()
    else:
        target.write_bytes(b"changed")
    assert current_components(paths)["bundled_xrizer"].startswith("invalid")
    runtime.install_openvr_runtime(paths)
    assert target.read_bytes() == original
    assert current_components(paths)["bundled_xrizer"] == runtime.OPENVR_RUNTIME_VERSION


@pytest.mark.parametrize("location", ["platform-compat", "meta"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_platform_shim_inventory_checks_both_installed_copies(paths, location, damage):
    source = paths.tools / "rift-runtime/LibOVRPlatformImpl64_1.dll"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"shim")
    copies = {
        "platform-compat": paths.tools / "platform-compat/LibOVRPlatformImpl64_1.dll",
        "meta": paths.prefix
        / "pfx/drive_c/Program Files/Oculus/Support/oculus-runtime/LibOVRPlatformImpl64_1.dll",
    }
    for target in copies.values():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"shim")
    assert runtime.platform_compat_current(paths)
    if damage == "missing":
        copies[location].unlink()
    else:
        copies[location].write_bytes(b"corrupt")
    assert not runtime.platform_compat_current(paths)
    assert current_components(paths)["platform_bridge"] == "missing/invalid"
