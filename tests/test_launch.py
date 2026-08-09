from pathlib import Path

from riftlift.config import Game, Paths
from riftlift.launch import launch
from riftlift.runtime import launch_environment


def test_injector_uses_existing_prefix_and_windows_game_path(tmp_path: Path, monkeypatch) -> None:
    paths = Paths(tmp_path / "data", tmp_path / "cache", tmp_path / "config", tmp_path / "games", tmp_path / "prefix", tmp_path / "tools")
    executable = paths.games / "sample/Binaries/Game.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"MZ")
    proton = tmp_path / "proton"
    revive = tmp_path / "revive"
    proton.mkdir()
    revive.mkdir()
    monkeypatch.setattr("riftlift.launch.install_proton", lambda _paths: proton)
    monkeypatch.setattr("riftlift.launch.install_revive", lambda _paths: revive)
    monkeypatch.setattr("riftlift.launch.launch_environment", lambda *_args: {})
    monkeypatch.setattr("riftlift.launch.shutil.which", lambda _name: None)
    captured = {}
    monkeypatch.setattr("riftlift.launch.subprocess.call", lambda command, **kwargs: captured.update(command=command, **kwargs) or 0)

    game = Game("sample", "Sample", "1", "sample-key", str(executable.parents[1]), "Binaries/Game.exe", ["-vr"])
    assert launch(paths, game, []) == 0
    assert captured["command"][1] == "runinprefix"
    assert "/wait" in captured["command"]
    game_path = captured["command"][captured["command"].index("sample-key") + 1]
    assert game_path.startswith("Z:\\")
    assert game_path.endswith("\\Binaries\\Game.exe")


def test_platform_shim_does_not_redirect_oculus_vr_runtime(tmp_path: Path, monkeypatch) -> None:
    paths = Paths(tmp_path / "data", tmp_path / "cache", tmp_path / "config", tmp_path / "games", tmp_path / "prefix", tmp_path / "tools")
    compatibility = paths.tools / "platform-compat"
    runtime = tmp_path / "openxr_monado.json"
    monkeypatch.setattr("riftlift.runtime.proton_environment", lambda *_args: {})
    monkeypatch.setattr("riftlift.runtime.active_runtime_json", lambda: runtime)
    monkeypatch.setattr("riftlift.runtime.install_platform_compat", lambda _paths: compatibility)

    environment = launch_environment(paths, paths.games / "sample", True)

    platform_path, runtime_path = environment["WINEPATH"].split(";")
    assert platform_path.endswith("\\platform-compat")
    assert runtime_path.endswith("\\Program Files\\Oculus\\Support\\oculus-runtime")
    assert "LIBOVR_DLL_DIR" not in environment
    assert environment["WINEDLLOVERRIDES"] == "d3d11=n;dxgi=n"
