import json
import os
from pathlib import Path

import pytest

from riftlift.config import Game, Paths
from riftlift.diagnostics import (
    clear_runtime_traces,
    recent_launches,
    trim_runtime_traces,
)
from riftlift.launch import (
    _clear_proton_openvr_cache,
    _expected_launch_components,
    _installed_openvr_build,
    _run_game_process,
    _terminate_marked_launch_processes,
    launch,
    oculus_launch_arguments,
    runtime_backend,
)
from riftlift.playtime import playtime
from riftlift.runtime import launch_environment, native_xr_bridge, setup
from riftlift.util import RiftLiftError


@pytest.fixture(autouse=True)
def fake_native_bridge(monkeypatch, tmp_path: Path) -> None:
    pe = tmp_path / "wineopenxr.dll"
    unix = tmp_path / "wineopenxr.so"
    monkeypatch.setattr(
        "riftlift.launch.native_xr_bridge",
        lambda *_args, **_kwargs: type("Bridge", (), {"pe": pe, "unix": unix})(),
    )
    monkeypatch.setattr("riftlift.launch.ensure_steam_running", lambda: None)


def test_native_xr_bridge_requires_wine_unixlib_pair(tmp_path: Path) -> None:
    proton = tmp_path / "GE-Proton"
    wine = proton / "files/lib/wine"
    pe = wine / "x86_64-windows/wineopenxr.dll"
    unix = wine / "x86_64-unix/wineopenxr.so"
    pe.parent.mkdir(parents=True)
    unix.parent.mkdir(parents=True)
    pe.write_bytes(b"MZ\0__wine_init_unix_call\0")
    unix.write_bytes(b"\x7fELF\0__wine_unix_call_funcs\0")

    bridge = native_xr_bridge(proton, "openxr")

    assert bridge.pe == pe
    assert bridge.unix == unix


def test_steamvr_build_identity_uses_valve_version_file(tmp_path: Path) -> None:
    steamvr = tmp_path / "SteamVR"
    (steamvr / "bin").mkdir(parents=True)
    (steamvr / "bin/version.txt").write_text("1781734990\n")

    assert _installed_openvr_build(steamvr, "steamvr") == "SteamVR 1781734990"


def test_missing_bundled_xrizer_is_not_reported_as_external(tmp_path: Path) -> None:
    assert _installed_openvr_build(tmp_path / "xrizer", "xrizer") == "missing"
    assert (
        _installed_openvr_build(tmp_path / "custom-runtime", "external")
        == "external-unversioned:custom-runtime"
    )


def test_steamvr_build_is_expected_as_captured_not_as_bundled_xrizer() -> None:
    components = {"openvr_runtime": "SteamVR 1781734990"}

    expected = _expected_launch_components(components, "steamvr")

    assert expected["openvr_runtime"] == "SteamVR 1781734990"


def test_runtime_trace_cleanup_covers_wines_local_temp_directory(
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
    trace = (
        paths.prefix
        / "pfx/drive_c/users/steamuser/AppData/Local/Temp/riftlift-runtime-trace.log"
    )
    trace.parent.mkdir(parents=True)
    trace.write_bytes(b"begin\n" + b"frame\n" * 200)
    monkeypatch.setattr("riftlift.diagnostics._MAX_RUNTIME_TRACE_BYTES", 128)

    trim_runtime_traces(paths)

    assert trace.stat().st_size <= 128
    assert trace.read_bytes().startswith(b"begin\n")
    clear_runtime_traces(paths)
    assert not trace.exists()


def test_native_xr_bridge_rejects_missing_or_non_native_halves(tmp_path: Path) -> None:
    proton = tmp_path / "GE-Proton"
    wine = proton / "files/lib/wine"
    pe = wine / "x86_64-windows/vrclient_x64.dll"
    unix = wine / "x86_64-unix/vrclient_x64.so"
    pe.parent.mkdir(parents=True)
    unix.parent.mkdir(parents=True)
    pe.write_bytes(b"MZ\0__wine_init_unix_call\0")

    with pytest.raises(RiftLiftError, match="missing its native OPENVR bridge"):
        native_xr_bridge(proton, "openvr")

    unix.write_bytes(b"not-elf")
    with pytest.raises(RiftLiftError, match="invalid binary format"):
        native_xr_bridge(proton, "openvr")

    unix.write_bytes(b"\x7fELF without a Wine export")
    with pytest.raises(RiftLiftError, match="not a Wine unixlib pair"):
        native_xr_bridge(proton, "openvr")


def test_launcher_uses_existing_prefix_and_windows_game_path(
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
    executable = paths.games / "sample/Binaries/Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    proton = tmp_path / "proton"
    rift_runtime = tmp_path / "rift_runtime"
    proton.mkdir()
    rift_runtime.mkdir()
    monkeypatch.setattr("riftlift.launch.install_proton", lambda _paths: proton)
    monkeypatch.setattr(
        "riftlift.launch.install_rift_runtime", lambda _paths: rift_runtime
    )
    captured = {}
    monkeypatch.setattr(
        "riftlift.launch.launch_environment",
        lambda *args: captured.update(environment_args=args) or {},
    )
    monkeypatch.setattr(
        "riftlift.launch._run_game_process",
        lambda command, **kwargs: captured.update(command=command, **kwargs) or 0,
    )

    game = Game(
        "sample",
        "Sample",
        "1",
        "sample-key",
        str(executable.parents[1]),
        "Binaries/Game.exe",
        ["-vr"],
    )
    assert launch(paths, game, []) == 0
    assert captured["command"][1] == "run"
    assert "/wait" in captured["command"]
    cwd_index = captured["command"].index("/cwd")
    assert captured["command"][cwd_index + 1].endswith("\\sample")
    game_path = captured["command"][cwd_index + 2]
    assert game_path.startswith("Z:\\")
    assert game_path.endswith("\\Binaries\\Game.exe")
    assert captured["environment_args"][-1] is True
    assert captured["env"]["DXVK_NO_VR"] == "1"
    assert captured["env"]["UMU_ID"] == "umu-default"
    assert captured["env"]["UMU_USE_STEAM"] == "0"
    launch_logs = list((paths.data / "diagnostics/logs").glob("launch-*.log"))
    assert len(launch_logs) == 1
    assert launch_logs[0].stat().st_mode & 0o777 == 0o600
    assert captured["stdout"].closed
    assert captured["stderr"] == -2  # subprocess.STDOUT
    assert playtime(paths, game.slug).launches == 1


def test_meta_game_ignores_steam_shortcut_id_for_proton_identity(
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
    executable = paths.games / "meta-game/Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    proton = tmp_path / "proton"
    runtime = tmp_path / "runtime"
    proton.mkdir()
    runtime.mkdir()
    monkeypatch.setattr("riftlift.launch.install_proton", lambda _paths: proton)
    monkeypatch.setattr("riftlift.launch.install_rift_runtime", lambda _paths: runtime)
    monkeypatch.setattr("riftlift.launch.launch_environment", lambda *_args: {})
    captured = {}
    monkeypatch.setattr(
        "riftlift.launch._run_game_process",
        lambda _command, **kwargs: captured.update(**kwargs) or 0,
    )
    game = Game(
        "meta-game",
        "Meta Game",
        "123",
        "meta-game",
        str(executable.parent),
        executable.name,
        [],
        steam_app_id=2581534236,
        source="meta",
    )

    assert launch(paths, game, []) == 0
    assert captured["env"].get("SteamAppId") in {None, "0"}
    assert captured["env"]["UMU_ID"] == "umu-default"
    assert captured["env"]["UMU_USE_STEAM"] == "0"


def test_openxr_launch_does_not_configure_a_second_openvr_client(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config/riftlift",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    executable = paths.games / "sample/Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    proton = tmp_path / "proton"
    rift_runtime = tmp_path / "rift-runtime"
    manifest = tmp_path / "openxr-runtime.json"
    proton.mkdir()
    rift_runtime.mkdir()
    manifest.write_text('{"runtime":{"library_path":"runtime.so"}}')
    monkeypatch.setattr("riftlift.launch.install_proton", lambda _paths: proton)
    monkeypatch.setattr(
        "riftlift.launch.install_rift_runtime", lambda _paths: rift_runtime
    )
    monkeypatch.setattr("riftlift.launch.runtime_backend", lambda _game: "openxr")
    monkeypatch.setattr(
        "riftlift.launch._clear_proton_openvr_cache",
        lambda *_args: pytest.fail("OpenXR launch performed OpenVR cache maintenance"),
    )
    monkeypatch.setattr(
        "riftlift.launch.launch_environment",
        lambda *_args: {"XR_RUNTIME_JSON": str(manifest)},
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "riftlift.launch._run_game_process",
        lambda _command, **kwargs: captured.update(**kwargs) or 0,
    )
    game = Game(
        "sample", "Sample", "1", "sample-key", str(executable.parent), "Game.exe", []
    )

    assert launch(paths, game, []) == 0
    assert captured["env"]["XR_RUNTIME_JSON"] == str(manifest)
    assert captured["env"]["VR_PATHREG_OVERRIDE"] == os.devnull
    assert "XDG_CONFIG_HOME" not in captured["env"]


def test_cancelled_launch_records_named_error(tmp_path: Path, monkeypatch) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    executable = paths.games / "sample/Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    proton = tmp_path / "proton"
    runtime = tmp_path / "runtime"
    proton.mkdir()
    runtime.mkdir()
    monkeypatch.setattr("riftlift.launch.install_proton", lambda _paths: proton)
    monkeypatch.setattr("riftlift.launch.install_rift_runtime", lambda _paths: runtime)
    monkeypatch.setattr("riftlift.launch.launch_environment", lambda *_args: {})
    monkeypatch.setattr(
        "riftlift.launch._run_game_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    game = Game(
        "sample",
        "Sample",
        "1",
        "sample-key",
        str(executable.parent),
        executable.name,
        [],
    )

    with pytest.raises(KeyboardInterrupt):
        launch(paths, game, [])

    assert recent_launches(paths)[0]["error"] == "KeyboardInterrupt"


def test_cancelled_process_terminates_the_game_process_group(monkeypatch) -> None:
    class Process:
        pid = 4321

        def __init__(self) -> None:
            self.waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt()
            assert timeout == 5
            return -15

        def poll(self):
            return None

    process = Process()
    options = {}
    monkeypatch.setattr(
        "riftlift.launch.subprocess.Popen",
        lambda *_args, **kwargs: options.update(kwargs) or process,
    )
    signals = []
    monkeypatch.setattr(
        "riftlift.launch.os.killpg", lambda pid, value: signals.append((pid, value))
    )
    monkeypatch.setattr("riftlift.launch._marked_launch_processes", lambda _id: [])

    with pytest.raises(KeyboardInterrupt):
        _run_game_process(["proton", "run", "game.exe"], launch_id="test-launch")

    assert options["start_new_session"] is True
    assert signals == [(4321, 15)]


def test_detached_launch_processes_are_stopped_by_their_marker(monkeypatch) -> None:
    remaining = iter(([123, 456], []))
    monkeypatch.setattr(
        "riftlift.launch._marked_launch_processes", lambda _id: next(remaining)
    )
    signals = []
    monkeypatch.setattr(
        "riftlift.launch.os.kill", lambda pid, value: signals.append((pid, value))
    )
    monkeypatch.setattr("riftlift.launch.time.sleep", lambda _seconds: None)

    _terminate_marked_launch_processes("owned-launch")

    assert signals == [(123, 15), (456, 15)]


def test_direct_openvr_bridge_uses_windows_action_manifest(
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
    executable = paths.games / "sample/Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    proton = tmp_path / "proton"
    rift_runtime = tmp_path / "rift_runtime"
    proton.mkdir()
    (rift_runtime / "Input").mkdir(parents=True)
    manifest = rift_runtime / "Input/action_manifest.json"
    manifest.write_text("{}")
    monkeypatch.setattr("riftlift.launch.install_proton", lambda _paths: proton)
    monkeypatch.setattr(
        "riftlift.launch.install_rift_runtime", lambda _paths: rift_runtime
    )
    monkeypatch.setattr("riftlift.launch.runtime_backend", lambda _game: "openvr")
    monkeypatch.setattr(
        "riftlift.launch.launch_environment",
        lambda *_args: {"XRIZER_LOG_DIR": "/tmp/xrizer"},
    )
    openvr = tmp_path / "xrizer"
    (openvr / "bin/linux64").mkdir(parents=True)
    (openvr / "bin/linux64/vrclient.so").write_bytes(b"ELF")
    monkeypatch.setenv("VR_OVERRIDE", str(openvr))

    game = Game(
        "sample", "Sample", "1", "sample-key", str(executable.parent), "Game.exe", []
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "riftlift.launch._run_game_process",
        lambda command, **kwargs: captured.update(command=command, **kwargs) or 0,
    )
    assert launch(paths, game, []) == 0
    assert captured["env"]["RIFTLIFT_ACTION_MANIFEST"] == (
        "Z:" + str(manifest.resolve()).replace("/", "\\")
    )
    assert "RIFTLIFT_XRIZER" not in captured["env"]
    assert "XRIZER_LOG_DIR" not in captured["env"]
    assert "XR_RUNTIME_JSON" not in captured["env"]
    assert "PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES" not in captured["env"]
    assert "OXR_ZERO_TIME_IS_NOW" not in captured["env"]
    assert captured["env"]["WINEDLLOVERRIDES"].split(";")[0] == "wineopenxr=d"
    assert captured["env"]["XDG_CONFIG_HOME"] == str(paths.config)


def test_xrizer_bridge_uses_host_action_manifest(tmp_path: Path, monkeypatch) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    executable = paths.games / "sample/Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    proton = tmp_path / "proton"
    rift_runtime = tmp_path / "rift_runtime"
    proton.mkdir()
    (rift_runtime / "Input").mkdir(parents=True)
    manifest = rift_runtime / "Input/action_manifest.json"
    manifest.write_text("{}")
    openvr = tmp_path / "xrizer"
    registry = tmp_path / "openvrpaths.vrpath"
    monkeypatch.setattr("riftlift.launch.install_proton", lambda _paths: proton)
    monkeypatch.setattr(
        "riftlift.launch.install_rift_runtime", lambda _paths: rift_runtime
    )
    monkeypatch.setattr("riftlift.launch.runtime_backend", lambda _game: "openvr")
    monkeypatch.setattr(
        "riftlift.launch.select_openvr_runtime",
        lambda *_args: (openvr, registry, "xrizer"),
    )
    monkeypatch.setattr(
        "riftlift.launch.launch_environment",
        lambda *_args: {"XRIZER_LOG_DIR": "/tmp/xrizer"},
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "riftlift.launch._run_game_process",
        lambda command, **kwargs: captured.update(command=command, **kwargs) or 0,
    )
    game = Game(
        "sample", "Sample", "1", "sample-key", str(executable.parent), "Game.exe", []
    )

    assert launch(paths, game, []) == 0
    assert captured["env"]["RIFTLIFT_ACTION_MANIFEST"] == str(manifest)
    assert captured["env"]["RIFTLIFT_XRIZER"] == "1"


def test_openvr_launch_clears_only_protons_generated_runtime_cache(
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
    wine = proton / "files/bin/wine"
    wine.parent.mkdir(parents=True)
    wine.write_bytes(b"ELF")
    generated = (
        paths.prefix
        / "pfx/drive_c/users/steamuser/AppData/Local/openvr/openvrpaths.vrpath"
    )
    generated.parent.mkdir(parents=True)
    generated.write_text("generated")
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(command=command, **kwargs)

    monkeypatch.setattr("riftlift.launch.subprocess.run", fake_run)
    monkeypatch.setenv("LD_PRELOAD", "/tmp/desktop-injector.so")

    _clear_proton_openvr_cache(paths, proton)

    assert captured["command"] == [
        str(wine),
        "reg.exe",
        "delete",
        r"HKCU\Software\Wine\VR",
        "/f",
    ]
    assert captured["env"]["WINEPREFIX"] == str(paths.prefix / "pfx")
    assert "LD_PRELOAD" not in captured["env"]
    assert captured["check"] is False
    assert not generated.exists()


def test_platform_shim_does_not_redirect_oculus_vr_runtime(
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
    compatibility = paths.tools / "platform-compat"
    runtime = tmp_path / "openxr_monado.json"
    monkeypatch.setattr("riftlift.runtime.proton_environment", lambda *_args: {})
    monkeypatch.setattr("riftlift.runtime.active_runtime_json", lambda: runtime)
    monkeypatch.setattr(
        "riftlift.runtime.install_platform_compat", lambda _paths: compatibility
    )

    environment = launch_environment(paths, paths.games / "sample", True)

    assert environment["WINEPATH"] == (
        r"C:\Program Files\Oculus\Support\oculus-runtime"
    )
    assert "platform-compat" not in environment["WINEPATH"]
    assert int(environment["RIFTLIFT_USER_ID"]) > 0
    assert "LIBOVR_DLL_DIR" not in environment
    assert environment["WINEDLLOVERRIDES"] == "d3d11=n;dxgi=n"


def test_platform_identity_is_persistent_and_overrideable(
    tmp_path: Path, monkeypatch
) -> None:
    from riftlift.xr_runtime import platform_user_id

    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    generated = platform_user_id(paths)
    assert platform_user_id(paths) == generated
    assert (paths.config / "platform-user-id").read_text().strip() == generated
    assert (paths.config / "platform-user-id").stat().st_mode & 0o777 == 0o600

    monkeypatch.setenv("RIFTLIFT_USER_ID", "35227")
    assert platform_user_id(paths) == "35227"


def test_active_runtime_uses_explicit_standard_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = tmp_path / "custom-monado.json"
    runtime.write_text(
        '{"file_format_version":"1.0.0","runtime":{"library_path":"libopenxr_monado.so"}}'
    )
    monkeypatch.setenv("XR_RUNTIME_JSON", str(runtime))

    from riftlift.xr_runtime import active_runtime_json

    assert active_runtime_json() == runtime.resolve()


def test_active_runtime_uses_xdg_active_manifest(tmp_path: Path, monkeypatch) -> None:
    config_home = tmp_path / "config"
    manifest = config_home / "openxr/1/active_runtime.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    monkeypatch.delenv("XR_RUNTIME_JSON", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "system-config"))

    from riftlift.xr_runtime import active_runtime_json

    assert active_runtime_json() == manifest.resolve()


def test_active_runtime_does_not_override_runtime_manager_selection(
    tmp_path: Path, monkeypatch
) -> None:
    config_home = tmp_path / "config"
    active = config_home / "openxr/1/active_runtime.json"
    stale_architecture_override = config_home / "openxr/1/active_runtime.x86_64.json"
    active.parent.mkdir(parents=True)
    active.write_text("{}")
    stale_architecture_override.write_text("{}")
    monkeypatch.delenv("XR_RUNTIME_JSON", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    from riftlift.xr_runtime import active_runtime_json

    assert active_runtime_json() == active.resolve()


def test_launch_environment_uses_selected_manifest_without_vendor_config(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "rift-data",
        tmp_path / "rift-cache",
        tmp_path / "rift-config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    manifest = tmp_path / "openxr.json"
    manifest.write_text("{}")
    monkeypatch.setattr("riftlift.runtime.proton_environment", lambda *_args: {})

    environment = launch_environment(
        paths, paths.games / "sample", False, runtime=manifest
    )

    assert environment["XR_RUNTIME_JSON"] == str(manifest)
    assert "DRI_PRIME" not in environment
    assert "LD_LIBRARY_PATH" not in environment


def test_explicit_runtime_selection_does_not_duplicate_loader_validation(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = tmp_path / "broken-monado.json"
    runtime.write_text(
        '{"file_format_version":"1.0.0","runtime":{'
        '"library_path":"../missing/libopenxr_monado.so"}}'
    )
    monkeypatch.setenv("XR_RUNTIME_JSON", str(runtime))

    from riftlift.xr_runtime import active_runtime_json

    assert active_runtime_json() == runtime.resolve()


def test_active_runtime_does_not_guess_from_vendor_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    config_home = tmp_path / "config"
    envision = config_home / "envision/envision.json"
    envision.parent.mkdir(parents=True)
    envision.write_text(
        json.dumps(
            {
                "selected_profile_uuid": "clean-profile",
                "user_profiles": [
                    {
                        "uuid": "clean-profile",
                        "name": "Clean Monado",
                        "prefix": str(tmp_path / "envision-prefix"),
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "system-config"))
    monkeypatch.delenv("XR_RUNTIME_JSON", raising=False)

    from riftlift.xr_runtime import active_runtime_json

    with pytest.raises(RiftLiftError, match="no active OpenXR runtime"):
        active_runtime_json()


def test_setup_does_not_require_an_active_openxr_runtime(
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
    actions: list[str] = []
    monkeypatch.setattr(
        "riftlift.runtime.active_runtime_json",
        lambda: (_ for _ in ()).throw(AssertionError("runtime discovery during setup")),
    )
    monkeypatch.setattr(
        "riftlift.runtime.install_proton",
        lambda _paths: actions.append("proton") or tmp_path / "proton",
    )
    monkeypatch.setattr(
        "riftlift.runtime.install_meta_runtime",
        lambda _paths: actions.append("meta"),
    )
    monkeypatch.setattr(
        "riftlift.runtime.install_rift_runtime",
        lambda _paths: actions.append("rift"),
    )
    monkeypatch.setattr(
        "riftlift.runtime.install_openvr_runtime",
        lambda _paths: actions.append("openvr"),
    )
    monkeypatch.setattr(
        "riftlift.runtime.install_platform_compat",
        lambda _paths: actions.append("platform"),
    )
    monkeypatch.setattr(
        "riftlift.runtime.shutdown_compat_prefix",
        lambda _paths, _proton: actions.append("shutdown"),
    )

    setup(paths)

    assert actions == ["proton", "meta", "rift", "openvr", "platform", "shutdown"]


def test_launch_has_no_device_specific_wrapper(tmp_path: Path, monkeypatch) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    executable = paths.games / "sample/Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    proton = tmp_path / "proton"
    rift_runtime = tmp_path / "rift_runtime"
    proton.mkdir()
    rift_runtime.mkdir()
    monkeypatch.setattr("riftlift.launch.install_proton", lambda _paths: proton)
    monkeypatch.setattr(
        "riftlift.launch.install_rift_runtime", lambda _paths: rift_runtime
    )
    monkeypatch.setattr("riftlift.launch.launch_environment", lambda *_args: {})
    monkeypatch.delenv("RIFTLIFT_LAUNCH_WRAPPER", raising=False)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "riftlift.launch._run_game_process",
        lambda command, **kwargs: captured.update(command=command, **kwargs) or 0,
    )

    game = Game(
        "sample",
        "Sample",
        "1",
        "sample-key",
        str(executable.parent),
        executable.name,
        [],
    )
    assert launch(paths, game, []) == 0
    assert captured["command"][0] == str(proton / "proton")


def test_openvr_backend_uses_packaged_translator_by_default(
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
    executable = paths.games / "sample/Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    proton = tmp_path / "proton"
    rift_runtime = tmp_path / "rift_runtime"
    proton.mkdir()
    rift_runtime.mkdir()
    monkeypatch.setattr("riftlift.launch.install_proton", lambda _paths: proton)
    monkeypatch.setattr(
        "riftlift.launch.install_rift_runtime", lambda _paths: rift_runtime
    )
    packaged_openvr = tmp_path / "packaged-openvr"
    monkeypatch.setattr(
        "riftlift.runtime.install_openvr_runtime", lambda _paths: packaged_openvr
    )
    runtime = tmp_path / "openxr_monado.json"
    runtime.write_text('{"runtime":{"name":"Monado","library_path":"libmonado.so"}}')
    monkeypatch.setattr("riftlift.runtime.active_runtime_json", lambda: runtime)
    monkeypatch.setattr("riftlift.launch.launch_environment", lambda *_args: {})
    monkeypatch.setenv("RIFTLIFT_RUNTIME_BACKEND", "openvr")
    monkeypatch.delenv("VR_OVERRIDE", raising=False)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "riftlift.launch._run_game_process",
        lambda command, **kwargs: captured.update(command=command, **kwargs) or 0,
    )

    game = Game(
        "sample",
        "Sample",
        "1",
        "sample-key",
        str(executable.parent),
        executable.name,
        [],
    )
    assert launch(paths, game, []) == 0
    command = captured["command"]
    assert command[command.index("/wait") + 1] == "/openvr"
    assert command[1] == "run"
    assert "DXVK_NO_VR" not in captured["env"]
    assert captured["env"]["VR_OVERRIDE"] == str(packaged_openvr)
    assert captured["env"]["RIFTLIFT_XRIZER"] == "1"
    assert captured["env"]["VR_PATHREG_OVERRIDE"] == str(
        paths.config / "openvr/openvrpaths.vrpath"
    )
    assert captured["env"]["XDG_CONFIG_HOME"] == str(paths.config)
    assert captured["env"]["UMU_ID"] == "umu-default"
    assert captured["env"]["UMU_USE_STEAM"] == "0"
    assert "PROTON_VR_RUNTIME" not in captured["env"]


def test_dual_runtime_game_uses_openvr_bridge_without_title_rules(
    tmp_path: Path, monkeypatch
) -> None:
    game_dir = tmp_path / "generic-game"
    executable = game_dir / "Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    (game_dir / "Plugins").mkdir()
    (game_dir / "Plugins/openvr_api.dll").write_bytes(b"")
    game = Game(
        "generic",
        "Generic",
        "1",
        "steam.app.1",
        str(game_dir),
        executable.name,
        [],
    )
    monkeypatch.delenv("RIFTLIFT_RUNTIME_BACKEND", raising=False)

    assert runtime_backend(game) == "openvr"


def test_oculus_only_game_uses_openxr_bridge(tmp_path: Path, monkeypatch) -> None:
    game_dir = tmp_path / "generic-game"
    executable = game_dir / "Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    game = Game(
        "generic",
        "Generic",
        "1",
        "generic-key",
        str(game_dir),
        executable.name,
        [],
    )
    monkeypatch.delenv("RIFTLIFT_RUNTIME_BACKEND", raising=False)

    assert runtime_backend(game) == "openxr"


def test_legacy_ovr_presentation_uses_openvr_bridge_without_title_rules(
    tmp_path: Path, monkeypatch
) -> None:
    game_dir = tmp_path / "generic-game"
    executable = game_dir / "Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    game = Game(
        "generic",
        "Generic",
        "1",
        "generic-key",
        str(game_dir),
        executable.name,
        ["-archive", "assets/toc", "-ovr", "-vr_presentation"],
    )
    monkeypatch.delenv("RIFTLIFT_RUNTIME_BACKEND", raising=False)

    assert runtime_backend(game) == "openvr"


def test_d3d12_oculus_game_uses_openvr_bridge_without_title_rules(
    tmp_path: Path, monkeypatch
) -> None:
    game_dir = tmp_path / "generic-game"
    executable = game_dir / "Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ\0unrelated\0D3D12.dll\0")
    game = Game(
        "generic",
        "Generic",
        "1",
        "generic-key",
        str(game_dir),
        executable.name,
        [],
    )
    monkeypatch.delenv("RIFTLIFT_RUNTIME_BACKEND", raising=False)

    assert runtime_backend(game) == "openvr"


def test_unity_oculus_xr_provider_uses_openvr_bridge_without_title_rules(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("RIFTLIFT_RUNTIME_BACKEND", raising=False)
    game_dir = tmp_path / "generic-unity-game"
    executable = game_dir / "Game.exe"
    plugin = game_dir / "Game_Data/Plugins/x86_64/OculusXRPlugin.dll"
    plugin.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    plugin.write_bytes(b"MZ")
    game = Game(
        "generic-unity-game",
        "Generic Unity Game",
        "1",
        "generic-unity-key",
        str(game_dir),
        executable.name,
        [],
    )

    assert runtime_backend(game) == "openvr"


def test_d3d11_import_wins_over_incidental_d3d12_string(
    tmp_path: Path, monkeypatch
) -> None:
    game_dir = tmp_path / "generic-game"
    executable = game_dir / "Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ\0engine supports D3D12.dll\0")
    game = Game(
        "generic",
        "Generic",
        "1",
        "generic-key",
        str(game_dir),
        executable.name,
        [],
    )
    monkeypatch.delenv("RIFTLIFT_RUNTIME_BACKEND", raising=False)
    monkeypatch.setattr(
        "riftlift.detection._pe_imported_dlls",
        lambda _path: {"d3d11.dll", "d3d12.dll"},
    )

    assert runtime_backend(game) == "openxr"


def test_unreal_launch_forces_vr_oculus_mode_without_title_rules(
    tmp_path: Path,
) -> None:
    game_dir = tmp_path / "generic-game"
    executable = game_dir / "Game/Binaries/Win64/Game-Win64-Shipping.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    game = Game(
        "generic",
        "Generic",
        "1",
        "steam.app.1",
        str(game_dir),
        executable.relative_to(game_dir).as_posix(),
        ["-steamvr", "-log"],
    )

    assert oculus_launch_arguments(game, []) == ["-log", "-vr", "-oculus"]


def test_unity_launch_replaces_runtime_selector_without_title_rules(
    tmp_path: Path,
) -> None:
    game_dir = tmp_path / "generic-game"
    executable = game_dir / "Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    (game_dir / "Game_Data").mkdir()
    game = Game(
        "generic",
        "Generic",
        "1",
        "steam.app.1",
        str(game_dir),
        executable.name,
        ["-vrmode", "OpenVR", "-log"],
    )

    assert oculus_launch_arguments(game, []) == ["-log", "-vrmode", "Oculus"]


def test_steam_game_keeps_steam_identity(tmp_path: Path, monkeypatch) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    executable = paths.games / "sample/Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    proton = tmp_path / "proton"
    rift_runtime = tmp_path / "rift_runtime"
    proton.mkdir()
    rift_runtime.mkdir()
    monkeypatch.setattr("riftlift.launch.install_proton", lambda _paths: proton)
    monkeypatch.setattr(
        "riftlift.launch.install_rift_runtime", lambda _paths: rift_runtime
    )
    monkeypatch.setattr(
        "riftlift.launch.launch_environment",
        lambda *_args: {"SteamAppId": "0", "SteamGameId": "0"},
    )
    captured: dict[str, object] = {}

    def fake_call(command, **kwargs):
        marker = executable.parent / "steam_appid.txt"
        captured.update(
            command=command,
            marker_during_launch=marker.read_text(),
            **kwargs,
        )
        return 0

    monkeypatch.setattr("riftlift.launch._run_game_process", fake_call)
    game = Game(
        "sample",
        "Sample",
        "732690",
        "steam.app.732690",
        str(executable.parent),
        executable.name,
        [],
        steam_app_id=732690,
        source="steam",
    )

    assert launch(paths, game, []) == 0
    assert captured["env"]["SteamAppId"] == "732690"
    assert captured["env"]["SteamGameId"] == "732690"
    assert captured["env"]["UMU_ID"] == "umu-732690"
    assert captured["env"]["UMU_USE_STEAM"] == "0"
    assert captured["marker_during_launch"] == "732690\n"
    assert not (executable.parent / "steam_appid.txt").exists()


def test_steam_game_preserves_existing_appid_marker(
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
    executable = paths.games / "sample/Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    marker = executable.parent / "steam_appid.txt"
    marker.write_text("user-owned\n")
    proton = tmp_path / "proton"
    runtime = tmp_path / "runtime"
    proton.mkdir()
    runtime.mkdir()
    monkeypatch.setattr("riftlift.launch.install_proton", lambda _paths: proton)
    monkeypatch.setattr("riftlift.launch.install_rift_runtime", lambda _paths: runtime)
    monkeypatch.setattr("riftlift.launch.launch_environment", lambda *_args: {})
    monkeypatch.setattr(
        "riftlift.launch._run_game_process",
        lambda *_args, **_kwargs: 0,
    )
    game = Game(
        "sample",
        "Sample",
        "732690",
        "steam.app.732690",
        str(executable.parent),
        executable.name,
        [],
        steam_app_id=732690,
        source="steam",
    )

    assert launch(paths, game, []) == 0
    assert marker.read_text() == "user-owned\n"


def test_local_game_does_not_inherit_verified_rift_offline_mode(
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
    executable = tmp_path / "local/Game.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"MZ")
    proton = tmp_path / "proton"
    rift_runtime = tmp_path / "rift_runtime"
    proton.mkdir()
    rift_runtime.mkdir()
    monkeypatch.setattr("riftlift.launch.install_proton", lambda _paths: proton)
    monkeypatch.setattr(
        "riftlift.launch.install_rift_runtime", lambda _paths: rift_runtime
    )
    captured = {}
    monkeypatch.setattr(
        "riftlift.launch.launch_environment",
        lambda *args: captured.update(environment_args=args) or {},
    )
    monkeypatch.setattr(
        "riftlift.launch._run_game_process", lambda *_args, **_kwargs: 0
    )
    game = Game(
        "local",
        "Local",
        "",
        "local.local",
        str(executable.parent),
        executable.name,
        [],
        source="local",
    )

    assert launch(paths, game, []) == 0
    assert captured["environment_args"][-1] is False
