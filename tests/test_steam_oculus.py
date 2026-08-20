import struct
from pathlib import Path

import pytest

from riftlift.config import Game, Paths
from riftlift.steam_oculus import (
    add_steam_game,
    game_from_steam_command,
    steam_library_roots,
    steam_oculus_game,
    steam_oculus_games,
)
from riftlift.util import RiftLiftError


def _pe64(path: Path, payload: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray(0x86)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 0x80)
    header[0x80:0x86] = b"PE\0\0\x64\x86"
    path.write_bytes(header + payload)


def _manifest(
    root: Path, install_dir: str, app_id: str, name: str | None = None
) -> Path:
    steamapps = root / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    target = steamapps / f"appmanifest_{app_id}.acf"
    target.write_text(
        '"AppState"\n{\n'
        f'  "appid" "{app_id}"\n'
        f'  "name" "{name or install_dir}"\n'
        f'  "installdir" "{install_dir}"\n'
        '  "buildid" "8267878"\n}\n'
    )
    return steamapps / "common" / install_dir


def test_discovers_unity_oculus_game_without_title_or_app_id_rules(
    tmp_path: Path,
) -> None:
    directory = _manifest(tmp_path, "CanvasVR", "1001")
    _pe64(directory / "CanvasVR.exe")
    _pe64(directory / "CanvasVR_Data/Plugins/x86_64/OVRPlugin.dll")

    game = steam_oculus_games(tmp_path)[0]

    assert game.app_id == "1001"
    assert game.app_key == "steam.app.1001"
    assert game.executable == "CanvasVR.exe"
    assert game.version == "8267878"


def test_discovers_modern_nested_steam_library_paths(tmp_path: Path) -> None:
    main = tmp_path / "main"
    secondary = tmp_path / "secondary"
    (main / "steamapps").mkdir(parents=True)
    (secondary / "steamapps").mkdir(parents=True)
    (main / "steamapps/libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n'
        '  "0" { "path" "' + str(main) + '" }\n'
        '  "1" { "path" "' + str(secondary) + '" }\n'
        '  "contentstatsid" "123"\n'
        "}\n"
    )

    assert steam_library_roots(main) == [main.resolve(), secondary.resolve()]


def test_discovers_unreal_oculus_game_and_shipping_binary(tmp_path: Path) -> None:
    directory = _manifest(tmp_path, "SpaceQuest", "1002")
    _pe64(directory / "SpaceQuest.exe")
    _pe64(directory / "SpaceQuest/Binaries/Win64/SpaceQuest-Win64-Shipping.exe")
    _pe64(directory / "Engine/Binaries/ThirdParty/Oculus/OVRPlugin/Win64/OVRPlugin.dll")

    game = steam_oculus_games(tmp_path)[0]

    assert game.executable == "SpaceQuest/Binaries/Win64/SpaceQuest-Win64-Shipping.exe"
    assert game.arguments == ["-vr"]


def test_discovers_native_oculus_sdk_import(tmp_path: Path) -> None:
    directory = _manifest(tmp_path, "NativeWorld", "1003")
    _pe64(directory / "NativeWorld.exe", b"\0LibOVRRT64_1.dll\0")

    game = steam_oculus_games(tmp_path)[0]

    assert game.executable == "NativeWorld.exe"


def test_steam_expanded_command_selects_actual_executable_and_arguments(
    tmp_path: Path,
) -> None:
    directory = _manifest(tmp_path, "MultiLaunch", "1004")
    _pe64(directory / "Default.exe", b"\0LibOVRRT64_1.dll\0")
    alternate = directory / "Binaries/Alternate.exe"
    _pe64(alternate)
    game = steam_oculus_games(tmp_path)[0]

    selected = game_from_steam_command(
        game,
        ["--", "/runtime/proton", "waitforexitandrun", str(alternate), "-mode", "vr"],
    )

    assert selected.executable == "Binaries/Alternate.exe"
    assert selected.arguments == ["-mode", "vr"]


def test_steam_command_does_not_treat_an_argument_as_the_game(
    tmp_path: Path,
) -> None:
    directory = _manifest(tmp_path, "ToolArgument", "1005")
    game_executable = directory / "Game.exe"
    helper = directory / "Tools/Configurator.exe"
    _pe64(game_executable, b"\0LibOVRRT64_1.dll\0")
    _pe64(helper)
    game = steam_oculus_games(tmp_path)[0]

    selected = game_from_steam_command(
        game,
        ["--", str(game_executable), "--configuration-tool", str(helper)],
    )

    assert selected.executable == "Game.exe"
    assert selected.arguments == ["--configuration-tool", str(helper)]


def test_rejects_unknown_or_non_oculus_game(tmp_path: Path) -> None:
    _manifest(tmp_path, "DesktopGame", "123")
    _pe64(tmp_path / "steamapps/common/DesktopGame/DesktopGame.exe")
    with pytest.raises(RiftLiftError):
        steam_oculus_game("123", tmp_path)


def test_adds_steam_game_without_overwriting_same_named_rift_game(
    tmp_path: Path,
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    rift = Game("canvas", "Canvas", "123", "rift.canvas", "/rift", "game.exe", [])
    steam = Game(
        "canvas",
        "Canvas",
        "456",
        "steam.app.456",
        "/steam",
        "game.exe",
        [],
    )
    rift.save(paths)

    added = add_steam_game(paths, steam)
    refreshed = add_steam_game(paths, steam)

    assert added.slug == "canvas-steam"
    assert refreshed.slug == added.slug
    assert Game.load(paths, "canvas").app_key == "rift.canvas"
    assert Game.load(paths, "canvas-steam").app_key == "steam.app.456"
