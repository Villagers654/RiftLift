import sys
from pathlib import Path

import pytest

from riftlift import windows
from riftlift.config import Game, Paths
from riftlift.util import RiftLiftError, atomic_write_text

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Native Windows host tests"
)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("RIFTLIFT_HOME", str(tmp_path))
    return Paths.defaults()


def test_windows_paths_are_portable(paths, tmp_path):
    assert paths.data == tmp_path / "data"
    assert paths.config == tmp_path / "config"


def test_atomic_write_windows(tmp_path):
    target = tmp_path / "atomic.txt"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text() == "second"
    assert list(tmp_path.iterdir()) == [target]


def test_cli_help_has_native_backend(capsys):
    from riftlift.cli import main

    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
    assert "native Windows" in capsys.readouterr().out


def test_windows_uses_the_shared_product_version(capsys):
    from riftlift import __version__
    from riftlift.cli import main

    with pytest.raises(SystemExit) as result:
        main(["--version"])
    assert result.value.code == 0
    assert capsys.readouterr().out == f"riftlift {__version__}\n"


def test_windows_metadata_is_part_of_the_main_package():
    root = Path(__file__).resolve().parents[1]
    metadata = (root / "pyproject.toml").read_text()
    assert '"Operating System :: Microsoft :: Windows"' in metadata
    assert not (root / "WINDOWS.md").exists()


def test_add_local_preserves_game_binary(paths):
    executable = Path(sys.executable)
    before = executable.read_bytes()
    game = windows.add_local(paths, str(executable), "Smoke probe")
    assert Game.load(paths, game.slug).executable_path == executable
    assert not game.platform_shim and not game.platform_offline
    assert executable.read_bytes() == before


def test_add_local_rejects_non_pe(paths, tmp_path):
    fake = tmp_path / "fake.exe"
    fake.write_text("not a binary")
    with pytest.raises(RiftLiftError, match="x64 PE"):
        windows.add_local(paths, str(fake))


def test_payload_checksum_failure_writes_nothing(paths, tmp_path):
    fake = tmp_path / "fake.zip"
    fake.write_bytes(b"bad")
    with pytest.raises(RiftLiftError, match="SHA256"):
        windows.install_payload(paths, fake)
    assert not windows.runtime_dir(paths).exists()


def test_native_launch_builds_argv_without_shell_or_wine(paths):
    game = windows.add_local(paths, sys.executable, "Probe", arguments='"two words"')
    runtime = windows.runtime_dir(paths)
    runtime.mkdir(parents=True)
    for name in windows.FILES:
        (runtime / name).touch()
    argv = windows.launch_command(paths, game, "openxr", ["tail"])
    assert argv[1:6] == ["/openxr", "/wait", "/app", game.app_key, "/cwd"]
    assert argv[-2:] == ["two words", "tail"]
    assert "wine" not in argv and "proton" not in argv
    assert "LibOVRPlatformImpl64_1.dll" not in " ".join(argv)


def test_missing_runtime_stops_before_launch(paths, monkeypatch):
    game = windows.add_local(paths, sys.executable, "Probe")
    runtime = windows.runtime_dir(paths)
    runtime.mkdir(parents=True)
    for name in windows.FILES:
        (runtime / name).touch()
    monkeypatch.setattr(windows, "runtime_ready", lambda backend: False)
    monkeypatch.setattr(
        windows.subprocess, "run", lambda *a, **k: pytest.fail("started process")
    )
    with pytest.raises(RiftLiftError, match="connect the headset"):
        windows.launch(paths, game, "openxr")


def test_doctor_missing_runtime_is_not_success(paths, monkeypatch):
    monkeypatch.setattr(windows, "active_openxr", lambda: None)
    monkeypatch.setattr(windows, "active_openvr", lambda: None)
    report, code = windows.doctor(paths)
    assert code == 2
    assert "NOT REGISTERED" in report
    assert "NOT VERIFIED" in report


def test_doctor_requires_platform_dependencies_for_downloaded_games(paths, monkeypatch):
    game = windows.add_local(paths, sys.executable, "Probe")
    game.platform_shim = True
    game.save(paths)
    native = windows.runtime_dir(paths)
    native.mkdir(parents=True)
    for name in windows.FILES:
        (native / name).touch()
    monkeypatch.setattr(windows, "runtime_ready", lambda backend: True)
    monkeypatch.setattr(windows, "active_openxr", lambda: None)
    monkeypatch.setattr(windows, "active_openvr", lambda: None)
    report, status = windows.doctor(paths)
    assert status == 2
    assert "Platform compatibility: MISSING" in report


def test_gui_constructs_without_linux_imports(paths, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from riftlift.main_window import Window

    app = QApplication.instance() or QApplication([])
    window = Window()
    assert window.windowTitle() == "RiftLift"
    assert Window.__module__ == "riftlift.main_window"
    assert window.signin.isEnabled()
    assert window.debug_logging.isEnabled()
    assert not window.steam_games.isEnabled()
    assert window.library.count() == 0
    window.close()
    app.processEvents()


def test_shared_ui_backend_reports_failures(paths, monkeypatch):
    from riftlift import windows_ui_backend

    monkeypatch.setattr(windows, "doctor", lambda p: ("Missing runtime", 2))
    with pytest.raises(RiftLiftError, match="View Activity"):
        windows_ui_backend.doctor(paths)
    game = windows.add_local(paths, sys.executable, "Probe")
    monkeypatch.setattr(windows, "runtime_ready", lambda b: True)
    monkeypatch.setattr(windows, "launch", lambda *a, **k: 7)
    with pytest.raises(RiftLiftError, match="code 7"):
        windows_ui_backend.launch(paths, game, [])


def test_windows_playtime_locks_across_processes(paths):
    import subprocess

    from riftlift.playtime import playtime

    script = (
        "from riftlift.config import Paths; from riftlift.playtime import mark_launch; "
        "[mark_launch(Paths.defaults(), 'probe') for _ in range(5)]"
    )
    children = [subprocess.Popen([sys.executable, "-c", script]) for _ in range(3)]
    assert all(child.wait(timeout=30) == 0 for child in children)
    assert playtime(paths, "probe").launches == 15


def test_native_runtime_selection_is_automatic(paths, monkeypatch):
    monkeypatch.setenv("RIFTLIFT_WINDOWS_BACKEND", "openvr")
    game = windows.add_local(paths, sys.executable, "Probe")
    monkeypatch.setattr(windows, "active_openxr", lambda: None)
    monkeypatch.setattr(windows, "active_openvr", lambda: None)
    monkeypatch.setattr(windows, "runtime_ready", lambda backend: backend == "openvr")
    assert windows.select_backend(game) == "openvr"
    monkeypatch.setattr(windows, "runtime_ready", lambda backend: True)
    assert windows.select_backend(game) == "openxr"
    assert windows.select_backend(game, "openvr") == "openvr"
    with pytest.raises(RiftLiftError, match="backend"):
        windows.select_backend(game, "invalid")


def test_automatic_steamvr_uses_its_openvr_interface(paths, monkeypatch):
    game = windows.add_local(paths, sys.executable, "Probe")
    steamvr = paths.tools / "SteamVR"
    monkeypatch.setattr(windows, "runtime_ready", lambda backend: True)
    monkeypatch.setattr(
        windows, "active_openxr", lambda: steamvr / "steamxr_win64.json"
    )
    monkeypatch.setattr(windows, "active_openvr", lambda: steamvr)
    assert windows.select_backend(game) == "openvr"
    monkeypatch.setattr(
        windows, "active_openxr", lambda: paths.tools / "other/runtime.json"
    )
    assert windows.select_backend(game) == "openxr"


def test_openvr_finds_current_steamvr_layout(tmp_path, monkeypatch):
    import json

    runtime = tmp_path / "SteamVR"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin/vrclient_x64.dll").touch()
    registry = tmp_path / "openvrpaths.vrpath"
    registry.write_text(json.dumps({"runtime": [str(runtime)]}))
    monkeypatch.setenv("VR_PATHREG_OVERRIDE", str(registry))
    assert windows.active_openvr() == runtime


def test_simulator_uses_isolated_configuration(paths, tmp_path):
    import json

    from riftlift.windows_simulator import configure

    runtime = tmp_path / "SteamVR"
    for name in (
        "bin/win64/vrstartup.exe",
        "bin/vrclient_x64.dll",
        "drivers/null/bin/win64/driver_null.dll",
        "steamxr_win64.json",
    ):
        target = runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    environment = configure(paths, runtime)
    registry = json.loads(Path(environment["VR_PATHREG_OVERRIDE"]).read_text())
    assert registry["runtime"] == [str(runtime)]
    assert Path(registry["config"][0]).is_relative_to(paths.data)
    assert environment["XR_RUNTIME_JSON"] == str(runtime / "steamxr_win64.json")
    settings = Path(registry["config"][0]) / "steamvr.vrsettings"
    assert json.loads(settings.read_text())["driver_null"]["enable"]
    settings.write_text('{"custom": true}')
    configure(paths, runtime)
    assert json.loads(settings.read_text()) == {"custom": True}


def test_windows_browser_uses_os_default_without_owning_process(paths, monkeypatch):
    from riftlift import auth_browser

    opened = []
    monkeypatch.setattr(
        auth_browser.webbrowser, "open", lambda url: opened.append(url) or True
    )
    browser = auth_browser.default_browser()
    assert (
        auth_browser.launch_browser_login(paths, browser, "https://auth.meta.com/")
        is None
    )
    assert opened == ["https://auth.meta.com/"]


def test_download_error_is_concise_and_does_not_register_game(
    paths, monkeypatch, capsys
):
    from meta_pcvr_downloader.download import DownloadError

    def denied(*args, **kwargs):
        raise DownloadError("Meta refused the manifest")

    monkeypatch.setattr("riftlift.library.add", denied)
    assert windows.main(["add", "123456789"]) == 1
    assert "Meta refused the manifest" in capsys.readouterr().err
    assert not list((paths.data / "games").glob("*.json"))


def test_windows_download_preserves_manifest_and_enables_offline_compat(
    paths, monkeypatch
):
    from types import SimpleNamespace

    from riftlift import library

    monkeypatch.setattr(library, "runtime_access_token", lambda paths: "test-token")
    build = SimpleNamespace(app_name="Test Download", version="1.0")
    monkeypatch.setattr(library, "list_builds", lambda *args: [build])
    monkeypatch.setattr(library, "select_build", lambda *args: build)
    manifest = {
        "canonicalName": "publisher.test",
        "launchFile": "bin/game.exe",
        "launchParameters": '"two words"',
    }
    monkeypatch.setattr(library, "fetch_manifest", lambda *args: manifest)
    monkeypatch.setattr(library, "_best_executable", lambda *args: "bin/game.exe")
    monkeypatch.setattr(library, "populate_game_metadata", lambda *args: None)
    received = []
    monkeypatch.setattr(
        library, "Downloader", lambda *args: SimpleNamespace(run=received.append)
    )
    game = library.add(paths, "123456789")
    assert received == [manifest]
    assert game.app_key == "publisher.test"
    assert game.arguments == ["two words"]
    assert game.platform_shim and game.platform_offline
