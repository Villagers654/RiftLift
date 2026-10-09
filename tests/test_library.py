import hashlib
import os
import shutil
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from meta_pcvr_downloader.api import Build
from meta_pcvr_downloader.download import Downloader, DownloadError, _safe_destination

from riftlift.config import Game, Paths
from riftlift.library import (
    _best_executable,
    _download_path,
    _launch_arguments,
    add_local,
    default_download_workers,
    parse_download_progress,
    remove,
)


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-length paths")
def test_download_long_temporary_filename_and_resume(tmp_path, monkeypatch):
    root = tmp_path / "game"
    cache = tmp_path / "segments"
    cache.mkdir()
    relative = "x" * (251 - len(str(root)) - len(".bundle") - 1) + ".bundle"
    assert len(str(root / relative)) == 251
    assert len(str(root / (relative + f".{os.getpid()}.part"))) >= 260
    payload = b"cached game segment"
    digest = hashlib.sha256(payload).hexdigest()
    (cache / digest).write_bytes(payload)
    manifest = {
        "files": {
            relative: {
                "size": len(payload),
                "sha256": digest,
                "segments": [[0, digest]],
            }
        }
    }

    def no_network(*args, **kwargs):
        raise AssertionError("Cached recovery must not access the network")

    monkeypatch.setattr("urllib.request.urlopen", no_network)
    output = _download_path(root)
    downloader = Downloader(
        "", Build("1", "Test", "2", "1", 1), output, _download_path(cache), 1
    )
    downloader.run(manifest)
    target = output / relative
    assert target.read_bytes() == payload
    modified = target.stat().st_mtime_ns
    downloader.run(manifest)
    assert target.stat().st_mtime_ns == modified
    assert (cache / digest).read_bytes() == payload
    assert _download_path(output) == output
    with pytest.raises(DownloadError, match="Unsafe path"):
        _safe_destination(output, "../escape")


def test_finalization_is_acknowledged_before_install_record_and_metadata(
    tmp_path, monkeypatch
):
    from riftlift import library

    paths = Paths(
        *(
            tmp_path / name
            for name in ("data", "cache", "config", "games", "prefix", "tools")
        )
    )
    build = SimpleNamespace(app_name="Fixture game", version="1")
    monkeypatch.setattr(library, "account_tokens", lambda *_args: ["FIXTURE"])
    monkeypatch.setattr(library, "list_builds", lambda *args: [build])
    monkeypatch.setattr(library, "select_build", lambda *args: build)
    monkeypatch.setattr(
        library, "fetch_manifest", lambda *args: {"launchFile": "game.exe"}
    )

    class FixtureDownloader:
        def __init__(self, token, build, directory, cache, workers):
            self.directory = directory

        def run(self, manifest):
            _pe64(self.directory / "game.exe")

    monkeypatch.setattr(library, "Downloader", FixtureDownloader)
    record = paths.data / "games/fixture-game.json"
    stages = []

    def finalize():
        assert not record.exists()
        stages.append("finalizing")

    def metadata(paths, game):
        assert record.exists()
        assert stages == ["finalizing"]
        stages.append("metadata")

    monkeypatch.setattr(library, "populate_game_metadata", metadata)
    game = library.add(paths, "123456789", on_finalizing=finalize)
    assert game.slug == "fixture-game"
    assert stages == ["finalizing", "metadata"]


def test_download_workers_scale_with_available_cpus() -> None:
    assert default_download_workers(1) == 4
    assert default_download_workers(4) == 8
    assert default_download_workers(12) == 24
    assert default_download_workers(64) == 32


def test_parse_download_progress_recognizes_downloader_output_lines() -> None:
    assert parse_download_progress(
        "Preparing 640 unique segments with 8 workers..."
    ) == (
        "Preparing segments",
        0,
        640,
    )
    assert parse_download_progress("  segments 50/640 (3 cached)") == (
        "Downloading",
        50,
        640,
    )
    assert parse_download_progress("Assembling and validating 601 files...") == (
        "Assembling files",
        0,
        0,
    )
    assert parse_download_progress("  files 100/601 (2.34/5.00 GiB)") == (
        "Assembling files",
        2396,
        5120,
    )
    assert parse_download_progress("Downloading Epic Roller Coasters 8.11.2...") is None
    assert parse_download_progress("") is None


def test_remove_deletes_meta_game_files_but_not_local_game_files(
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
    meta_dir = tmp_path / "games/aircar"
    meta_dir.mkdir(parents=True)
    (meta_dir / "Aircar.exe").touch()
    meta_game = Game(
        "aircar", "Aircar", "1", "meta.aircar", str(meta_dir), "Aircar.exe", []
    )
    meta_game.save(paths)
    (paths.data / "artwork/aircar").mkdir(parents=True)
    (paths.data / "artwork/aircar/icon.png").touch()

    local_dir = tmp_path / "elsewhere/mygame"
    local_dir.mkdir(parents=True)
    (local_dir / "game.exe").touch()
    local_game = Game(
        "mygame",
        "My Game",
        "2",
        "local.mygame",
        str(local_dir),
        "game.exe",
        [],
        source="local",
    )
    local_game.save(paths)

    remove(paths, meta_game)
    assert not meta_dir.exists()
    assert not (paths.data / "artwork/aircar").exists()
    assert not (paths.data / "games/aircar.json").exists()

    remove(paths, local_game)
    assert local_dir.is_dir()
    assert (local_dir / "game.exe").is_file()
    assert not (paths.data / "games/mygame.json").exists()


def _meta_game(tmp_path: Path) -> tuple[Paths, Game]:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    game_dir = tmp_path / "games/aircar"
    game_dir.mkdir(parents=True)
    (game_dir / "Aircar.exe").touch()
    game = Game("aircar", "Aircar", "1", "meta.aircar", str(game_dir), "Aircar.exe", [])
    game.save(paths)
    return paths, game


def test_remove_keeps_the_record_when_game_files_cannot_be_deleted(
    tmp_path: Path, monkeypatch
) -> None:
    paths, game = _meta_game(tmp_path)
    real_rmtree = shutil.rmtree

    def failing_rmtree(path, *args, **kwargs):
        if Path(path) == game.game_dir:
            raise PermissionError("file in use")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("riftlift.library.shutil.rmtree", failing_rmtree)

    with pytest.raises(PermissionError):
        remove(paths, game)

    assert (paths.data / "games/aircar.json").exists()
    assert game.game_dir.is_dir()


def test_remove_succeeds_when_game_files_are_already_gone(tmp_path: Path) -> None:
    paths, game = _meta_game(tmp_path)
    (game.game_dir / "Aircar.exe").unlink()
    game.game_dir.rmdir()

    remove(paths, game)

    assert not (paths.data / "games/aircar.json").exists()


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


def test_unreal_shipping_binary_is_found_below_a_packaged_build_folder(
    tmp_path: Path,
) -> None:
    _pe64(tmp_path / "WindowsNoEditor/Adventure.exe")
    _pe64(
        tmp_path
        / "WindowsNoEditor/Adventure/Binaries/Win64/Adventure-Win64-Shipping.exe"
    )
    manifest = {"launchFile": "WindowsNoEditor\Adventure.exe"}

    executable = _best_executable(tmp_path, manifest, None)

    assert executable == (
        "WindowsNoEditor/Adventure/Binaries/Win64/Adventure-Win64-Shipping.exe"
    )
    assert _launch_arguments(tmp_path, executable, manifest, None) == ["-vr"]


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


def test_downloaded_game_executable_cannot_escape_its_folder(tmp_path: Path) -> None:
    game = tmp_path / "game"
    game.mkdir()
    _pe64(tmp_path / "outside.exe")

    with pytest.raises(ValueError, match="inside the game folder"):
        _best_executable(game, {"launchFile": "../outside.exe"}, None)


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


def test_install_falls_back_to_another_signed_in_account(tmp_path, monkeypatch):
    from riftlift import library

    paths = Paths(
        *(
            tmp_path / name
            for name in ("data", "cache", "config", "games", "prefix", "tools")
        )
    )
    build = SimpleNamespace(app_name="Fixture game", version="1")
    monkeypatch.setattr(library, "account_tokens", lambda *_args: ["OTHER", "OWNER"])
    monkeypatch.setattr(library, "list_builds", lambda *args: [build])
    monkeypatch.setattr(library, "select_build", lambda *args: build)

    def manifest(token, _build):
        if token != "OWNER":
            raise DownloadError("403 not entitled")
        return {"launchFile": "game.exe"}

    monkeypatch.setattr(library, "fetch_manifest", manifest)

    assert library._owned_build(paths, "1", None) == (
        "OWNER",
        build,
        {"launchFile": "game.exe"},
    )

    monkeypatch.setattr(library, "account_tokens", lambda *_args: ["OTHER"])
    with pytest.raises(DownloadError, match="not entitled"):
        library._owned_build(paths, "1", None)
