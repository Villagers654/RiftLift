import hashlib
import io
import json
import sys
import zipfile
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


@pytest.mark.parametrize("complete", [False, True])
def test_source_setup_completes_missing_meta_platform_dependencies(
    paths, tmp_path, monkeypatch, complete
):
    from riftlift import desktop_services

    names = windows.FILES | (
        windows.PLATFORM_FILES if complete else {"LibOVRPlatformImpl64_1.dll"}
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in names:
            bundle.writestr(name, b"fixture payload")
        bundle.writestr("Input/bindings.json", "{}")
    payload = archive.getvalue()
    package = tmp_path / "fixture.zip"
    package.write_bytes(payload)
    monkeypatch.setattr(windows, "PAYLOAD_SHA256", hashlib.sha256(payload).hexdigest())
    meta = tmp_path / "meta.zip"
    with zipfile.ZipFile(meta, "w") as bundle:
        for name in windows.PLATFORM_FILES - {"LibOVRPlatformImpl64_1_real.dll"}:
            bundle.writestr(name, b"original Meta payload")
    monkeypatch.setattr(
        windows,
        "SDK_RUNTIME_SHA256",
        hashlib.sha256(b"original Meta payload").hexdigest(),
    )
    fetched = []

    def fetch(url, target, expected_sha256):
        assert url.endswith("?id=3766757683456363")
        assert expected_sha256 == (
            "adbdc5f0285a2ac2ead6fdd34522de98de1bf6782017d9857ea4044b2d2fd009"
        )
        fetched.append(target)
        return meta

    monkeypatch.setattr(windows, "download", fetch)
    native = windows.install_payload(paths, package)
    assert native == windows.runtime_dir(paths)
    assert not desktop_services.needs_setup(paths)
    assert (native / "LibOVRPlatformImpl64_1.dll").read_bytes() == b"fixture payload"
    assert len(fetched) == (0 if complete else 1)
    if not complete:
        assert (native / "LibOVRPlatformImpl64_1_real.dll").read_bytes() == (
            b"original Meta payload"
        )
    windows.install_platform_dependencies(paths, native)
    assert len(fetched) == (0 if complete else 1)


def test_platform_dependency_validation_happens_before_writes(
    paths, tmp_path, monkeypatch
):
    archive = tmp_path / "meta.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in windows.PLATFORM_FILES - {"LibOVRPlatformImpl64_1_real.dll"}:
            bundle.writestr(name, b"invalid SDK runtime")
    monkeypatch.setattr(windows, "download", lambda *args: archive)
    native = windows.runtime_dir(paths)
    with pytest.raises(RiftLiftError, match="Meta SDK runtime checksum mismatch"):
        windows.install_platform_dependencies(paths, native)
    assert not native.exists()


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


def test_native_launch_preserves_saved_options(paths):
    game = windows.add_local(paths, sys.executable, "Probe", arguments="base")
    game.launch_options = ["--fixture", "two words"]
    runtime = windows.runtime_dir(paths)
    runtime.mkdir(parents=True)
    for name in windows.FILES:
        (runtime / name).touch()
    argv = windows.launch_command(paths, game, "openxr", ["tail"])
    assert argv[-4:] == ["base", "--fixture", "two words", "tail"]


def test_native_launch_preserves_saved_environment_without_starting_game(
    paths, monkeypatch
):
    from riftlift import windows_process

    game = windows.add_local(paths, sys.executable, "Probe")
    game.environment = {"RIFTLIFT_FIXTURE": "preserved"}
    monkeypatch.setenv("LOCALAPPDATA", str(paths.data))
    monkeypatch.setattr(windows, "launch_command", lambda *args: ["fixture.exe"])
    monkeypatch.setattr(windows, "runtime_ready", lambda backend: True)
    monkeypatch.setattr(windows, "install_sdk_runtime", lambda paths: paths.tools)
    captured = []
    monkeypatch.setattr(
        windows_process,
        "run_game",
        lambda command, **kwargs: captured.append(kwargs["env"]) or 0,
    )
    assert windows.launch(paths, game, "openxr") == 0
    assert captured[0]["RIFTLIFT_FIXTURE"] == "preserved"
    assert captured[0]["LIBOVR_DLL_DIR"] == str(paths.tools) + __import__("os").sep


def test_missing_runtime_stops_before_launch(paths, monkeypatch):
    game = windows.add_local(paths, sys.executable, "Probe")
    runtime = windows.runtime_dir(paths)
    runtime.mkdir(parents=True)
    for name in windows.FILES:
        (runtime / name).touch()
    monkeypatch.setattr(windows, "runtime_ready", lambda backend: False)
    monkeypatch.setattr(windows, "steamvr_openxr_manifest", lambda: None)
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
    assert window.settings_page.debug_logging.isEnabled()
    assert window.steam_games.isHidden()
    assert window._installed_category.childCount() == 0
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

    runtime = tmp_path / "SteamVR"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin/vrclient_x64.dll").touch()
    registry = tmp_path / "openvrpaths.vrpath"
    registry.write_text(json.dumps({"runtime": [str(runtime)]}))
    monkeypatch.setenv("VR_PATHREG_OVERRIDE", str(registry))
    assert windows.active_openvr() == runtime


def test_simulator_uses_isolated_configuration(paths, tmp_path):

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
    monkeypatch.setattr(auth_browser, "_windows_default_browser", lambda: None)
    monkeypatch.setattr(
        auth_browser.webbrowser, "open", lambda url: opened.append(url) or True
    )
    browser = auth_browser.default_browser()
    assert (
        auth_browser.launch_browser_login(paths, browser, "https://auth.meta.com/")
        is None
    )
    assert opened == ["https://auth.meta.com/"]


def test_windows_edge_login_uses_an_owned_profile(paths, monkeypatch):
    from riftlift import auth_browser

    edge = auth_browser.Browser("edge", "Microsoft Edge", "chromium", ("msedge",))
    launched = []
    monkeypatch.setattr(auth_browser, "_windows_default_browser", lambda: edge)
    monkeypatch.setattr(
        auth_browser.subprocess,
        "Popen",
        lambda command, **_options: launched.append(command) or object(),
    )

    assert auth_browser.default_browser() == edge
    auth_browser.launch_browser_login(paths, edge, "https://auth.meta.com/")

    assert any(argument.startswith("--user-data-dir=") for argument in launched[0])
    preferences = json.loads(
        (
            auth_browser.browser_home(paths, edge) / "profile/Default/Preferences"
        ).read_text()
    )
    assert preferences["protocol_handler"]["allowed_origin_protocol_pairs"][
        "https://auth.meta.com"
    ] == {"oculus": True, "oculus-client": True}


def test_windows_login_preserves_browser_profiles_and_protocol_preferences(
    paths, monkeypatch
):
    from riftlift import auth_browser

    monkeypatch.setattr(auth_browser, "_windows_default_browser", lambda: None)
    opened = []
    monkeypatch.setattr(
        auth_browser.webbrowser,
        "open",
        lambda url: opened.append(url) or True,
    )

    browser = auth_browser.default_browser()
    assert browser.family == "native"
    auth_browser.launch_browser_login(paths, browser, "https://auth.meta.com/")
    assert opened == ["https://auth.meta.com/"]
    assert not (paths.config / "auth").exists()


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


def test_ovrplugin_layer_and_steamvr_openxr_are_per_process(
    paths, tmp_path, monkeypatch
):
    steamvr = tmp_path / "SteamVR"
    steamvr.mkdir()
    (steamvr / "steamxr_win64.json").write_text("{}")
    runtime = windows.runtime_dir(paths)
    runtime.mkdir(parents=True)
    (runtime / windows.OPENXR_LAYER_FILE).touch()
    monkeypatch.setattr(windows, "active_openxr", lambda: None)
    monkeypatch.setattr(windows, "active_openvr", lambda: steamvr)

    updates = windows.openxr_environment(paths, {"XR_ENABLE_API_LAYERS": "Other"})

    assert updates["XR_RUNTIME_JSON"] == str(steamvr / "steamxr_win64.json")
    assert updates["XR_ENABLE_API_LAYERS"] == (
        windows.OPENXR_LAYER_NAME + __import__("os").pathsep + "Other"
    )
    manifest = Path(updates["XR_API_LAYER_PATH"]) / "riftlift-openxr-layer.json"
    layer = json.loads(manifest.read_text())["api_layer"]
    assert layer["name"] == windows.OPENXR_LAYER_NAME
    assert layer["library_path"] == str(runtime / windows.OPENXR_LAYER_FILE)


def test_registered_openxr_runtime_is_not_overridden(paths, tmp_path, monkeypatch):
    registered = tmp_path / "runtime.json"
    monkeypatch.setattr(windows, "active_openxr", lambda: registered)
    monkeypatch.setattr(windows, "active_openvr", lambda: tmp_path)
    (tmp_path / "steamxr_win64.json").write_text("{}")
    # The pinned source payload predates the layer: launch without it.
    assert windows.openxr_environment(paths, {}) == {}


def test_steamvr_sees_the_game_name_when_the_launcher_supports_it(paths):
    game = windows.add_local(paths, sys.executable, "Probe Game")
    runtime = windows.runtime_dir(paths)
    runtime.mkdir(parents=True)
    for name in windows.FILES:
        (runtime / name).touch()
    # The pinned source payload's launcher has no /manifest option.
    assert "/manifest" not in windows.launch_command(paths, game, "openvr")
    (runtime / "RiftLiftLauncher.exe").write_bytes("/manifest".encode("utf-16-le"))
    argv = windows.launch_command(paths, game, "openvr")
    manifest = Path(argv[argv.index("/manifest") + 1])
    assert argv.index("/manifest") < argv.index(str(game.executable_path.resolve()))
    application = json.loads(manifest.read_text())["applications"][0]
    assert application["app_key"] == "riftlift.app." + game.app_key
    assert application["strings"]["en_us"]["name"] == "Probe Game"
    assert "/manifest" not in windows.launch_command(paths, game, "openxr")


def test_openxr_backend_can_use_steamvr_when_nothing_is_registered(
    paths, tmp_path, monkeypatch
):
    from riftlift import windows_process

    steamvr = tmp_path / "SteamVR"
    steamvr.mkdir()
    (steamvr / "steamxr_win64.json").write_text("{}")
    game = windows.add_local(paths, sys.executable, "Probe")
    monkeypatch.setenv("LOCALAPPDATA", str(paths.data))
    monkeypatch.setattr(windows, "active_openxr", lambda: None)
    monkeypatch.setattr(windows, "active_openvr", lambda: steamvr)
    monkeypatch.setattr(windows, "launch_command", lambda *args: ["fixture.exe"])
    monkeypatch.setattr(windows, "install_sdk_runtime", lambda paths: paths.tools)
    captured = []
    monkeypatch.setattr(
        windows_process,
        "run_game",
        lambda command, **kwargs: captured.append(kwargs["env"]) or 0,
    )
    # Automatic selection keeps SteamVR on its OpenVR interface...
    assert windows.select_backend(game) == "openvr"
    # ...while an explicit OpenXR launch reaches SteamVR's OpenXR runtime.
    assert windows.launch(paths, game, "openxr") == 0
    assert captured[0]["XR_RUNTIME_JSON"] == str(steamvr / "steamxr_win64.json")
