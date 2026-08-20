import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from riftlift.config import Paths
from riftlift.runtime import (
    DXVK_VERSION,
    META_SIGNING_ROOT_PEM,
    META_SIGNING_ROOT_REGISTRY_KEY,
    META_SIGNING_ROOT_THUMBPRINT,
    OPENVR_RUNTIME_VERSION,
    PROTON_VERSION,
    RUNTIME_VERSION,
    MetaPackage,
    _install_meta_packages,
    _install_meta_signing_root,
    _meta_signing_root_der,
    _meta_signing_root_registry_blob,
    _safe_tar,
    initialize_prefix,
    install_dxvk_compat,
    install_meta_runtime,
    install_openvr_runtime,
    install_proton,
    install_rift_runtime,
    meta_signing_root_installed,
    proton_environment,
    select_openvr_runtime,
    shutdown_compat_prefix,
    steamvr_runtime_for_openxr,
)
from riftlift.util import RiftLiftError

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


def _dxvk_archive(path: Path, content: bytes) -> Path:
    archive = path / "dxvk.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        files = {
            "dxvk/VERSION": f"{DXVK_VERSION}\n".encode(),
            "dxvk/x64/d3d11.dll": b"MZ" + content + b"-x64-d3d11",
            "dxvk/x64/dxgi.dll": b"MZ" + content + b"-x64-dxgi",
            "dxvk/x32/d3d11.dll": b"MZ" + content + b"-x32-d3d11",
            "dxvk/x32/dxgi.dll": b"MZ" + content + b"-x32-dxgi",
        }
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
    return archive


def test_tar_extraction_rejects_special_files(tmp_path: Path) -> None:
    archive = tmp_path / "special.tar"
    with tarfile.open(archive, "w") as bundle:
        device = tarfile.TarInfo("device")
        device.type = tarfile.CHRTYPE
        bundle.addfile(device)

    with pytest.raises(RiftLiftError, match="unsafe entry"):
        _safe_tar(archive, tmp_path / "unpacked")


def test_incomplete_proton_archive_preserves_existing_install(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    target = tmp_path / "steam/compatibilitytools.d" / PROTON_VERSION
    target.mkdir(parents=True)
    (target / "working.txt").write_text("keep")
    archive = tmp_path / "incomplete-proton.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        payload = b"incomplete"
        info = tarfile.TarInfo(f"{PROTON_VERSION}/version")
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    monkeypatch.setattr("riftlift.runtime.steam_root", lambda: tmp_path / "steam")
    monkeypatch.setattr("riftlift.runtime.download", lambda *_args: archive)

    with pytest.raises(RiftLiftError, match="expected launcher"):
        install_proton(paths)

    assert (target / "working.txt").read_text() == "keep"


def test_dxvk_compat_installs_both_architectures_and_repairs_changes(
    tmp_path, monkeypatch
):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    proton = tmp_path / "GE-Proton"
    archive = _dxvk_archive(tmp_path, b"patched")
    monkeypatch.setenv("RIFTLIFT_DXVK_ARCHIVE", str(archive))

    destination = install_dxvk_compat(paths, proton)

    x64 = destination / "x86_64-windows/d3d11.dll"
    x32 = destination / "i386-windows/d3d11.dll"
    assert x64.read_bytes() == b"MZpatched-x64-d3d11"
    assert x32.read_bytes() == b"MZpatched-x32-d3d11"
    marker = json.loads((destination / ".riftlift-dxvk.json").read_text())
    assert marker["version"] == DXVK_VERSION
    assert set(marker["files"]) == {
        "x86_64-windows/d3d11.dll",
        "x86_64-windows/dxgi.dll",
        "i386-windows/d3d11.dll",
        "i386-windows/dxgi.dll",
    }

    x64.write_bytes(b"corrupt")
    install_dxvk_compat(paths, proton)
    assert x64.read_bytes() == b"MZpatched-x64-d3d11"


def test_incomplete_dxvk_archive_preserves_installed_payload(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    proton = tmp_path / "proton"
    destination = proton / "files/lib/wine/dxvk"
    destination.mkdir(parents=True)
    (destination / "working.txt").write_text("keep")
    archive = tmp_path / "incomplete-dxvk.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        payload = b"not a complete package"
        info = tarfile.TarInfo("dxvk/x64/d3d11.dll")
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    monkeypatch.setenv("RIFTLIFT_DXVK_ARCHIVE", str(archive))

    with pytest.raises(RiftLiftError, match="payload is incomplete"):
        install_dxvk_compat(paths, proton)

    assert (destination / "working.txt").read_text() == "keep"


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


def test_incomplete_runtime_archive_preserves_installed_payload(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    destination = paths.tools / "rift-runtime"
    destination.mkdir(parents=True)
    (destination / "working.txt").write_text("keep")
    archive = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("unrelated.txt", "bad")
    monkeypatch.setenv("RIFTLIFT_RUNTIME_ARCHIVE", str(archive))

    with pytest.raises(RiftLiftError, match="payload is incomplete"):
        install_rift_runtime(paths)

    assert (destination / "working.txt").read_text() == "keep"


def test_clean_prefix_initialization_bypasses_game_launcher(tmp_path, monkeypatch):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    captured: list[tuple[str, ...]] = []

    def fake_proton(_paths, *arguments, **_kwargs):
        captured.append(arguments)
        (paths.prefix / "pfx/drive_c").mkdir(parents=True)

    monkeypatch.setattr("riftlift.runtime.proton", fake_proton)

    initialize_prefix(paths)

    assert captured == [("runinprefix", "cmd.exe", "/c", "exit")]


def test_setup_shutdown_stops_only_the_shared_compat_prefix(tmp_path, monkeypatch):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    proton = tmp_path / "proton"
    wineserver = proton / "files/bin/wineserver"
    wineserver.parent.mkdir(parents=True)
    wineserver.write_bytes(b"ELF")
    monkeypatch.setenv("LD_PRELOAD", "/host/injector.so")
    captured = {}

    class Result:
        returncode = 0

    def fake_run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return Result()

    monkeypatch.setattr("riftlift.runtime.subprocess.run", fake_run)

    shutdown_compat_prefix(paths, proton)

    assert captured["command"] == [str(wineserver), "-k", "-w"]
    assert captured["env"]["WINEPREFIX"] == str(paths.prefix / "pfx")
    assert "LD_PRELOAD" not in captured["env"]
    assert captured["timeout"] == 20


def test_setup_shutdown_accepts_an_already_idle_prefix(tmp_path, monkeypatch):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    proton = tmp_path / "proton"
    wineserver = proton / "files/bin/wineserver"
    wineserver.parent.mkdir(parents=True)
    wineserver.write_bytes(b"ELF")

    class Result:
        returncode = 1

    monkeypatch.setattr(
        "riftlift.runtime.subprocess.run", lambda _command, **_kwargs: Result()
    )

    shutdown_compat_prefix(paths, proton)


def test_meta_runtime_disables_vendor_vr_service(tmp_path, monkeypatch):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    support = paths.prefix / "pfx/drive_c/Program Files/Oculus/Support"
    support.mkdir(parents=True)
    legacy_client = support / "oculus-client"
    legacy_client.mkdir()
    (legacy_client / "Client.exe").write_bytes(b"unused desktop client")
    captured: list[tuple[str, ...]] = []

    monkeypatch.setattr("riftlift.runtime.META_PACKAGES", ())
    monkeypatch.setattr("riftlift.runtime.patch_meta_runtime", lambda _path: None)
    monkeypatch.setattr(
        "riftlift.runtime.proton",
        lambda _paths, *arguments, **_kwargs: captured.append(arguments),
    )
    install_meta_runtime(paths)

    assert not legacy_client.exists()
    service = r"HKLM\System\CurrentControlSet\Services\OVRService"
    assert (
        "runinprefix",
        "reg.exe",
        "add",
        service,
        "/v",
        "Start",
        "/t",
        "REG_DWORD",
        "/d",
        "4",
        "/f",
    ) in captured
    assert (support / ".riftlift-registry-v4").read_text() == "1\n"


def test_meta_runtime_repairs_a_corrupt_signed_loader(tmp_path, monkeypatch):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    support = paths.prefix / "pfx/drive_c/Program Files/Oculus/Support"
    runtime = support / "oculus-runtime"
    runtime.mkdir(parents=True)
    expected = b"signed LibOVR loader"
    archive = tmp_path / "oculus-runtime.pkg"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("LibOVRRT64_1.dll", expected)
    package_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    (runtime / ".riftlift-package.json").write_text(
        '{"sha256": "' + package_hash + '"}\n'
    )
    (runtime / "LibOVRRT64_1.dll").write_bytes(b"corrupt")

    monkeypatch.setattr(
        "riftlift.runtime.META_PACKAGES",
        (MetaPackage("oculus-runtime", "test", package_hash),),
    )
    monkeypatch.setattr(
        "riftlift.runtime.META_RUNTIME_SIGNED_FILES",
        {"LibOVRRT64_1.dll": hashlib.sha256(expected).hexdigest()},
    )
    monkeypatch.setattr("riftlift.runtime.download", lambda *_args: archive)
    monkeypatch.setattr("riftlift.runtime.patch_meta_runtime", lambda _path: None)
    monkeypatch.setattr("riftlift.runtime._install_meta_signing_root", lambda *_: None)
    monkeypatch.setattr("riftlift.runtime.proton", lambda *_args, **_kwargs: None)

    install_meta_runtime(paths)

    assert (runtime / "LibOVRRT64_1.dll").read_bytes() == expected


def test_meta_runtime_installs_required_signing_root(tmp_path, monkeypatch):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    support = paths.prefix / "pfx/drive_c/Program Files/Oculus/Support"
    runtime = support / "oculus-runtime"
    runtime.mkdir(parents=True)
    captured: list[tuple[str, ...]] = []
    monkeypatch.setattr("riftlift.runtime._signed_meta_runtime_current", lambda _: True)
    monkeypatch.setattr(
        "riftlift.runtime.proton",
        lambda _paths, *arguments, **_kwargs: captured.append(arguments),
    )
    # A stale marker must not hide a missing Wine certificate-store entry.
    (support / ".riftlift-meta-signing-root-v2").write_text(
        f"{META_SIGNING_ROOT_THUMBPRINT}\n"
    )

    _install_meta_signing_root(paths, support)

    assert captured[0][:5] == (
        "runinprefix",
        "reg.exe",
        "add",
        META_SIGNING_ROOT_REGISTRY_KEY,
        "/v",
    )
    assert captured[0][5] == "Blob"
    assert captured[0][6:9] == ("/t", "REG_BINARY", "/d")
    blob = bytes.fromhex(captured[0][9])
    assert blob == _meta_signing_root_registry_blob()
    assert blob.endswith(_meta_signing_root_der())
    assert captured[1] == (
        "runinprefix",
        "reg.exe",
        "query",
        META_SIGNING_ROOT_REGISTRY_KEY,
        "/v",
        "Blob",
    )
    assert (
        support / ".riftlift-meta-signing-root-v2"
    ).read_text().strip() == META_SIGNING_ROOT_THUMBPRINT


def test_incomplete_meta_package_preserves_installed_payload(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    support = tmp_path / "support"
    destination = support / "oculus-platform-runtime"
    destination.mkdir(parents=True)
    (destination / "working.txt").write_text("keep")
    archive = tmp_path / "incomplete.pkg"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("unrelated.txt", "bad")
    package = MetaPackage("oculus-platform-runtime", "test", "test-sha")
    monkeypatch.setattr("riftlift.runtime.META_PACKAGES", (package,))
    monkeypatch.setattr("riftlift.runtime.download", lambda *_args: archive)

    with pytest.raises(RiftLiftError, match=r"package.*is incomplete"):
        _install_meta_packages(paths, support)

    assert (destination / "working.txt").read_text() == "keep"


def test_meta_signing_root_matches_pinned_thumbprint() -> None:
    certificate = x509.load_pem_x509_certificate(META_SIGNING_ROOT_PEM.encode())

    assert (
        certificate.fingerprint(hashes.SHA1()).hex().upper()
        == META_SIGNING_ROOT_THUMBPRINT
    )


def test_meta_signing_root_check_reads_actual_wine_store(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    registry = paths.prefix / "pfx/system.reg"
    registry.parent.mkdir(parents=True)
    registry.write_text("Wine Registry Version 2\n")

    assert not meta_signing_root_installed(paths)

    registry.write_text(
        r"[Software\\Microsoft\\SystemCertificates\\Root\\Certificates\\"
        + META_SIGNING_ROOT_THUMBPRINT
        + "]\n"
        + '"Blob"=hex:03,00,00,00\n'
    )

    assert meta_signing_root_installed(paths)


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
    registry = json.loads((paths.config / "openvr/openvrpaths.vrpath").read_text())
    assert registry["runtime"] == [str(destination)]
    assert registry["config"] == [str(paths.config / "openvr/runtime")]
    assert registry["log"] == [str(paths.data / "diagnostics/openvr")]
    archive.unlink()
    assert install_openvr_runtime(paths) == destination


def test_incomplete_openvr_archive_preserves_installed_payload(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    destination = paths.tools / "openvr-runtime"
    destination.mkdir(parents=True)
    (destination / "working.txt").write_text("keep")
    archive = tmp_path / "incomplete.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        payload = b"bad"
        info = tarfile.TarInfo("xrizer/unrelated.txt")
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    monkeypatch.setenv("RIFTLIFT_OPENVR_RUNTIME_ARCHIVE", str(archive))

    with pytest.raises(RiftLiftError, match="payload is incomplete"):
        install_openvr_runtime(paths)

    assert (destination / "working.txt").read_text() == "keep"


def test_steamvr_openxr_manifest_selects_valve_openvr_directly(tmp_path, monkeypatch):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    steam = tmp_path / "Steam"
    steamvr = steam / "steamapps/common/SteamVR"
    (steamvr / "bin/linux64").mkdir(parents=True)
    (steamvr / "bin/linux64/vrclient.so").write_bytes(b"ELF")
    (steam / "config").mkdir()
    (steam / "logs").mkdir()
    manifest = steamvr / "steamxr_linux64.json"
    manifest.write_text(
        json.dumps(
            {
                "runtime": {
                    "name": "SteamVR",
                    "VALVE_runtime_is_steamvr": True,
                    "library_path": "bin/linux64/vrclient.so",
                }
            }
        )
    )
    monkeypatch.delenv("VR_OVERRIDE", raising=False)
    monkeypatch.delenv("VR_PATHREG_OVERRIDE", raising=False)
    monkeypatch.setattr(
        "riftlift.runtime.install_openvr_runtime",
        lambda _paths: (_ for _ in ()).throw(AssertionError("XRizer was selected")),
    )

    selected, registry_path, kind = select_openvr_runtime(paths, manifest)

    assert steamvr_runtime_for_openxr(manifest) == steamvr.resolve()
    assert selected == steamvr.resolve()
    assert kind == "steamvr"
    registry = json.loads(registry_path.read_text())
    assert registry["runtime"] == [str(steamvr.resolve())]
    assert registry["config"] == [str(steam / "config")]
    assert registry["log"] == [str(steam / "logs")]


def test_non_steamvr_openxr_runtime_keeps_bundled_xrizer(tmp_path, monkeypatch):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    manifest = tmp_path / "openxr_monado.json"
    manifest.write_text(json.dumps({"runtime": {"name": "Monado"}}))
    xrizer = tmp_path / "xrizer"
    monkeypatch.delenv("VR_OVERRIDE", raising=False)
    monkeypatch.delenv("VR_PATHREG_OVERRIDE", raising=False)
    monkeypatch.setattr(
        "riftlift.runtime.install_openvr_runtime", lambda _paths: xrizer
    )

    selected, registry_path, kind = select_openvr_runtime(paths, manifest)

    assert selected == xrizer
    assert kind == "xrizer"
    registry = json.loads(registry_path.read_text())
    assert registry["runtime"] == [str(xrizer)]
    assert registry["config"] == [str(paths.config / "openvr/runtime")]


def test_explicit_openvr_registry_is_mirrored_to_private_fallback(
    tmp_path, monkeypatch
):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    steamvr = tmp_path / "SteamVR"
    (steamvr / "bin/linux64").mkdir(parents=True)
    (steamvr / "bin/linux64/vrclient.so").write_bytes(b"ELF")
    (steamvr / "steamxr_linux64.json").write_text(
        json.dumps({"runtime": {"name": "SteamVR"}})
    )
    selected_registry = tmp_path / "isolated/openvrpaths.vrpath"
    selected_registry.parent.mkdir()
    payload = {
        "version": 1,
        "runtime": [str(steamvr)],
        "config": [str(tmp_path / "isolated/config")],
        "log": [str(tmp_path / "isolated/logs")],
        "external_drivers": [str(tmp_path / "virtual-hmd")],
    }
    selected_registry.write_text(json.dumps(payload))
    monkeypatch.setenv("VR_OVERRIDE", str(steamvr))
    monkeypatch.setenv("VR_PATHREG_OVERRIDE", str(selected_registry))

    selected, registry_path, kind = select_openvr_runtime(paths)

    assert selected == steamvr.resolve()
    assert kind == "steamvr"
    assert registry_path == paths.config / "openvr/openvrpaths.vrpath"
    assert json.loads(registry_path.read_text()) == payload


def test_proton_debug_logs_use_diagnostics_directory(tmp_path, monkeypatch):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    steam = tmp_path / "steam"
    monkeypatch.setattr("riftlift.runtime.steam_root", lambda: steam)
    monkeypatch.setenv("RIFTLIFT_PROTON_LOG", "1")

    environment = proton_environment(paths)

    assert environment["PROTON_LOG"] == "1"
    assert environment["PROTON_LOG_DIR"] == str(paths.data / "diagnostics/proton")
    for name in ("proton", "graphics", "crashes"):
        directory = paths.data / "diagnostics" / name
        assert directory.is_dir()
        assert directory.stat().st_mode & 0o777 == 0o700


def test_gui_debug_setting_enables_bounded_proton_logging(tmp_path, monkeypatch):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    monkeypatch.setattr("riftlift.runtime.steam_root", lambda: tmp_path / "steam")
    paths.config.mkdir(parents=True)
    (paths.config / "debug-logging").write_text("1\n")
    monkeypatch.delenv("RIFTLIFT_PROTON_LOG", raising=False)
    monkeypatch.delenv("RIFTLIFT_WINEDEBUG", raising=False)

    environment = proton_environment(paths)

    assert environment["PROTON_LOG"] == "1"
    assert "+openxr" in environment["WINEDEBUG"]
    assert "+vrclient" in environment["WINEDEBUG"]
    assert "+steamclient" in environment["WINEDEBUG"]
    assert "+vulkan" not in environment["WINEDEBUG"]
    assert "+module" not in environment["WINEDEBUG"]
    assert "+wintrust" in environment["WINEDEBUG"]
    assert "+crypt" in environment["WINEDEBUG"]
    assert "+chain" in environment["WINEDEBUG"]
    assert environment["DXVK_LOG_LEVEL"] == "debug"
    assert environment["VKD3D_DEBUG"] == "info"
    assert environment["VK_LOADER_DEBUG"] == "error,warn,info"
    assert environment["XR_LOADER_DEBUG"] == "all"
    assert environment["RUST_LOG"] == "info,xrizer_tracking=debug"
    assert environment["XRIZER_LOG_DIR"].endswith("diagnostics/openvr")
    assert environment["DXVK_LOG_PATH"].endswith("diagnostics/graphics")
    assert environment["PROTON_CRASH_REPORT_DIR"].endswith("diagnostics/crashes")

    monkeypatch.setenv("RIFTLIFT_RUST_LOG", "xrizer=trace")
    assert proton_environment(paths)["RUST_LOG"] == "xrizer=trace"

    monkeypatch.setenv("RIFTLIFT_PROTON_LOG", "0")
    environment = proton_environment(paths)
    assert environment["PROTON_LOG"] == "0"
