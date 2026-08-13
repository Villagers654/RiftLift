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
    initialize_prefix,
    proton_environment,
    shutdown_compat_prefix,
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
    assert "+vulkan" in environment["WINEDEBUG"]
    assert environment["DXVK_LOG_LEVEL"] == "debug"
    assert environment["VKD3D_DEBUG"] == "info"
    assert environment["VK_LOADER_DEBUG"] == "error,warn,info"
    assert environment["XR_LOADER_DEBUG"] == "all"
    assert environment["DXVK_LOG_PATH"].endswith("diagnostics/graphics")
    assert environment["PROTON_CRASH_REPORT_DIR"].endswith("diagnostics/crashes")

    monkeypatch.setenv("RIFTLIFT_PROTON_LOG", "0")
    environment = proton_environment(paths)
    assert environment["PROTON_LOG"] == "0"
