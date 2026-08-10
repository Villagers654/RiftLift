from pathlib import Path

import pytest

from riftlift.steam_oculus import steam_oculus_game, steam_oculus_games
from riftlift.util import RiftLiftError


def _install(root: Path, app_id: str = "1920760") -> None:
    steamapps = root / "steamapps"
    plugins = steamapps / "common/StereoPaint/StereoPaint_Data/Plugins/x86_64"
    plugins.mkdir(parents=True)
    (plugins / "OVRPlugin.dll").touch()
    (plugins / "OculusXRPlugin.dll").touch()
    (steamapps / "common/StereoPaint/StereoPaint.exe").touch()
    (steamapps / f"appmanifest_{app_id}.acf").write_text(
        '"AppState"\n{\n'
        f'  "appid" "{app_id}"\n'
        '  "name" "StereoPaint"\n'
        '  "installdir" "StereoPaint"\n'
        '  "buildid" "8267878"\n}\n'
    )


def test_discovers_unity_oculus_xr_game(tmp_path: Path) -> None:
    _install(tmp_path)
    game = steam_oculus_games(tmp_path)[0]
    assert game.app_id == "1920760"
    assert game.app_key == "steam.app.1920760"
    assert game.executable == "StereoPaint.exe"
    assert game.version == "8267878"


def test_rejects_unknown_or_non_oculus_game(tmp_path: Path) -> None:
    (tmp_path / "steamapps").mkdir()
    with pytest.raises(RiftLiftError):
        steam_oculus_game("123", tmp_path)
