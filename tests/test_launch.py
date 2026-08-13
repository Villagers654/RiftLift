import json
from pathlib import Path

import pytest

from riftlift.config import Game, Paths
from riftlift.diagnostics import (
    clear_runtime_traces,
    recent_launches,
    trim_runtime_traces,
)
from riftlift.launch import (
    _clear_stale_openvr_registry,
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
    monkeypatch.setattr(
        "riftlift.launch.install_openvr_runtime", lambda _paths: tmp_path / "xrizer"
    )
    captured = {}
    monkeypatch.setattr(
        "riftlift.launch.launch_environment",
        lambda *args: captured.update(environment_args=args) or {},
    )
    monkeypatch.setattr(
        "riftlift.launch.subprocess.call",
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
        "riftlift.launch.subprocess.call",
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
        "riftlift.launch.subprocess.call",
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


def test_openvr_bridge_uses_bundled_action_manifest(
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
    monkeypatch.setattr("riftlift.launch.launch_environment", lambda *_args: {})
    monkeypatch.setenv("VR_OVERRIDE", "/opt/xrizer")

    game = Game(
        "sample", "Sample", "1", "sample-key", str(executable.parent), "Game.exe", []
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "riftlift.launch.subprocess.call",
        lambda command, **kwargs: captured.update(command=command, **kwargs) or 0,
    )
    assert launch(paths, game, []) == 0
    assert captured["env"]["RIFTLIFT_ACTION_MANIFEST"] == str(manifest)


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
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(command=command, **kwargs)

    monkeypatch.setattr("riftlift.launch.subprocess.run", fake_run)
    monkeypatch.setenv("LD_PRELOAD", "/tmp/desktop-injector.so")

    _clear_stale_openvr_registry(paths, proton)

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
    from riftlift.runtime import platform_user_id

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

    from riftlift.runtime import active_runtime_json

    assert active_runtime_json() == runtime.resolve()


def test_active_runtime_uses_selected_envision_profile_without_shell_exports(
    tmp_path: Path, monkeypatch
) -> None:
    config_home = tmp_path / "config"
    data_home = tmp_path / "data"
    prefix = data_home / "envision/prefixes/clean_profile"
    manifest = prefix / "share/openxr/1/openxr_monado.json"
    library = prefix / "lib64/libopenxr_monado.so"
    manifest.parent.mkdir(parents=True)
    library.parent.mkdir(parents=True)
    library.write_bytes(b"\x7fELF")
    manifest.write_text(
        '{"file_format_version":"1.0.0","runtime":{'
        '"name":"Monado","library_path":"../../../lib64/libopenxr_monado.so"}}'
    )
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
                        "prefix": str(prefix),
                        "environment": {"DRI_PRIME": "1", "XRT_TEST": "selected"},
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.delenv("XR_RUNTIME_JSON", raising=False)

    from riftlift.runtime import active_runtime_json

    assert active_runtime_json() == manifest.resolve()


def test_launch_environment_imports_selected_envision_profile(
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
    config_home = tmp_path / "config"
    data_home = tmp_path / "data"
    prefix = data_home / "envision/prefixes/profile-id"
    manifest = prefix / "share/openxr/1/openxr_monado.json"
    library = prefix / "lib64/libopenxr_monado.so"
    manifest.parent.mkdir(parents=True)
    library.parent.mkdir(parents=True)
    library.write_bytes(b"\x7fELF")
    manifest.write_text(
        '{"file_format_version":"1.0.0","runtime":{'
        '"name":"Monado","library_path":"../../../lib64/libopenxr_monado.so"}}'
    )
    envision = config_home / "envision/envision.json"
    envision.parent.mkdir(parents=True)
    envision.write_text(
        json.dumps(
            {
                "selected_profile_uuid": "profile-id",
                "user_profiles": [
                    {
                        "uuid": "profile-id",
                        "prefix": str(prefix),
                        "environment": {
                            "DRI_PRIME": "pci-0000_03_00_0",
                            "XRT_COMPOSITOR_COMPUTE": "1",
                        },
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.delenv("XR_RUNTIME_JSON", raising=False)
    monkeypatch.delenv("DRI_PRIME", raising=False)
    monkeypatch.setattr("riftlift.runtime.steam_root", lambda: tmp_path / "steam")

    environment = launch_environment(paths, paths.games / "sample", False)

    assert environment["XR_RUNTIME_JSON"] == str(manifest.resolve())
    assert environment["DRI_PRIME"] == "pci-0000_03_00_0"
    assert environment["XRT_COMPOSITOR_COMPUTE"] == "1"
    assert str(prefix / "lib64") in environment["LD_LIBRARY_PATH"].split(":")


def test_explicit_runtime_rejects_missing_relative_library(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = tmp_path / "broken-monado.json"
    runtime.write_text(
        '{"file_format_version":"1.0.0","runtime":{'
        '"library_path":"../missing/libopenxr_monado.so"}}'
    )
    monkeypatch.setenv("XR_RUNTIME_JSON", str(runtime))

    from riftlift.runtime import active_runtime_json

    with pytest.raises(RiftLiftError, match="points to a missing library"):
        active_runtime_json()


def test_unbuilt_selected_envision_profile_has_actionable_error(
    tmp_path: Path, monkeypatch
) -> None:
    config_home = tmp_path / "config"
    data_home = tmp_path / "data"
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
                        "prefix": str(data_home / "envision/prefixes/clean-profile"),
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.delenv("XR_RUNTIME_JSON", raising=False)

    from riftlift.runtime import active_runtime_json

    with pytest.raises(
        RiftLiftError, match="selected profile 'Clean Monado'.*not built"
    ):
        active_runtime_json()


def test_setup_checks_openxr_before_installing_components(
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
        lambda: (_ for _ in ()).throw(RiftLiftError("no runtime")),
    )
    monkeypatch.setattr(
        "riftlift.runtime.install_proton", lambda _paths: actions.append("proton")
    )

    try:
        setup(paths)
    except RiftLiftError as error:
        assert str(error) == "no runtime"
    else:
        raise AssertionError("setup unexpectedly continued without OpenXR")
    assert actions == []


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
        "riftlift.launch.subprocess.call",
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
        "riftlift.launch.install_openvr_runtime", lambda _paths: packaged_openvr
    )
    monkeypatch.setattr("riftlift.launch.launch_environment", lambda *_args: {})
    monkeypatch.setenv("RIFTLIFT_RUNTIME_BACKEND", "openvr")
    monkeypatch.delenv("VR_OVERRIDE", raising=False)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "riftlift.launch.subprocess.call",
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
    assert captured["env"]["DXVK_NO_VR"] == "1"
    assert captured["env"]["VR_OVERRIDE"] == str(packaged_openvr)
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
    monkeypatch.setattr(
        "riftlift.launch.subprocess.call",
        lambda command, **kwargs: captured.update(command=command, **kwargs) or 0,
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
    assert captured["env"]["SteamAppId"] == "732690"
    assert captured["env"]["SteamGameId"] == "732690"


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
    monkeypatch.setattr("riftlift.launch.subprocess.call", lambda *_args, **_kwargs: 0)
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
