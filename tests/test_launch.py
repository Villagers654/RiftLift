from pathlib import Path

import pytest

from riftlift.config import Game, Paths
from riftlift.launch import launch, oculus_launch_arguments, runtime_backend
from riftlift.playtime import playtime
from riftlift.runtime import launch_environment, setup
from riftlift.util import RiftLiftError, linux_to_windows


class FakeNativeHost:
    class Endpoint:
        runtime_name = "Test OpenXR"

        @staticmethod
        def environment() -> dict[str, str]:
            return {
                "RIFTLIFT_RUNTIME_PROTOCOL": "2",
                "RIFTLIFT_RUNTIME_ENDPOINT": "127.0.0.1:12345",
                "RIFTLIFT_RUNTIME_TOKEN": "test-token",
            }

    endpoint = Endpoint()

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def fake_native_runtime(monkeypatch) -> None:
    monkeypatch.setattr(
        "riftlift.launch.NativeRuntimeHost.start",
        lambda *_args, **_kwargs: FakeNativeHost(),
    )


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
    assert playtime(paths, game.slug).launches == 1


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


def test_launch_accepts_explicit_openvr_backend(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setenv("RIFTLIFT_RUNTIME_BACKEND", "openvr")
    monkeypatch.setenv("VR_OVERRIDE", "/opt/xrizer")
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
    assert captured["env"]["VR_OVERRIDE"] == "/opt/xrizer"
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
