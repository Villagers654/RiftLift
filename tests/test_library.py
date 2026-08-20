import struct
from pathlib import Path

from riftlift.config import Paths
from riftlift.library import (
    _best_executable,
    _launch_arguments,
    add_local,
    default_download_workers,
)


def test_download_workers_scale_with_available_cpus() -> None:
    assert default_download_workers(1) == 4
    assert default_download_workers(4) == 8
    assert default_download_workers(12) == 24
    assert default_download_workers(64) == 32


def _pe64(path: Path, payload: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray(0x86)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 0x80)
    header[0x80:0x86] = b"PE\0\0\x64\x86"
    path.write_bytes(header + payload)


def test_unreal_shipping_binary_is_discovered_without_an_app_allowlist(
    tmp_path: Path,
) -> None:
    _pe64(tmp_path / "Adventure.exe")
    _pe64(tmp_path / "Adventure/Binaries/Win64/Adventure-Win64-Shipping.exe")
    manifest = {"launchFile": "Adventure.exe", "launchParameters": "-log"}

    executable = _best_executable(tmp_path, manifest, None)

    assert executable == "Adventure/Binaries/Win64/Adventure-Win64-Shipping.exe"
    assert _launch_arguments(tmp_path, executable, manifest, None) == ["-log", "-vr"]


def test_manifest_executable_and_arguments_remain_authoritative_for_native_game(
    tmp_path: Path,
) -> None:
    _pe64(tmp_path / "NativeGame.exe")
    manifest = {"launchFile": "NativeGame.exe", "launchParameters": '"-mode=vr"'}

    executable = _best_executable(tmp_path, manifest, None)

    assert executable == "NativeGame.exe"
    assert _launch_arguments(tmp_path, executable, manifest, None) == ["-mode=vr"]


def test_explicit_overrides_remain_available(tmp_path: Path) -> None:
    _pe64(tmp_path / "Alternate.exe")
    manifest = {"launchFile": "Missing.exe", "launchParameters": "-ignored"}

    executable = _best_executable(tmp_path, manifest, "Alternate.exe")

    assert executable == "Alternate.exe"
    assert _launch_arguments(tmp_path, executable, manifest, "--custom value") == [
        "--custom",
        "value",
    ]


def test_launch_arguments_remove_grouping_quotes_and_keep_windows_paths(
    tmp_path: Path,
) -> None:
    _pe64(tmp_path / "Game.exe")
    manifest = {
        "launchFile": "Game.exe",
        "launchParameters": r'--region "US East" --config C:\Games\Rift\game.ini',
    }

    assert _launch_arguments(tmp_path, "Game.exe", manifest, None) == [
        "--region",
        "US East",
        "--config",
        r"C:\Games\Rift\game.ini",
    ]


def test_add_local_registers_existing_game_without_copying_it(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "managed-games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    root = tmp_path / "installed/ready-at-dawn-echo-arena"
    executable = root / "bin/win10/echovr.exe"
    _pe64(executable)

    game = add_local(
        paths,
        executable,
        name="Echo VR",
        arguments='-noovr --region "US East"',
        version="test",
    )

    assert game.slug == "echo-vr"
    assert game.source == "local"
    assert game.game_dir == root.resolve()
    assert game.executable == "bin/win10/echovr.exe"
    assert game.arguments == ["-noovr", "--region", "US East"]
    assert game.app_key == "ready-at-dawn-echo-arena"
    assert not game.platform_offline
    assert executable.is_file()
    assert not paths.games.exists() or not any(paths.games.iterdir())


def test_add_local_rejects_executable_outside_selected_root(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "managed-games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    executable = tmp_path / "elsewhere/game.exe"
    _pe64(executable)
    root = tmp_path / "other-root"
    root.mkdir()

    try:
        add_local(paths, executable, root=root)
    except ValueError as error:
        assert "inside the local game folder" in str(error)
    else:
        raise AssertionError("outside executable was accepted")
