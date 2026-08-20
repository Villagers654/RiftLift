from pathlib import Path

import pytest

from riftlift.config import Game, Paths
from riftlift.steam import (
    _existing_by_slug,
    _install_wayvr_metadata,
    _same_user_process_running,
    _shortcut,
    _shortcut_games,
    ensure_steam_running,
    sync_with_restart,
    user_config,
)
from riftlift.util import RiftLiftError, installed_command


def game() -> Game:
    return Game(
        "example", "Example VR", "123", "example", "/games/example", "game.exe", []
    )


def test_existing_shortcut_id_is_preserved() -> None:
    existing = {
        "0": {
            "appid": 4214331913,
            "LaunchOptions": "launch example",
            "tags": {"0": "VR", "1": "RiftLift"},
        }
    }
    prior = _existing_by_slug(existing)["example"]
    result = _shortcut(game(), Path("/home/person/.local/bin/riftlift"), prior["appid"])
    assert result["appid"] == 4214331913


def test_shortcut_uses_catalog_icon() -> None:
    value = game()
    value.artwork = {"icon": "/metadata/example/icon.png"}
    value.genres = ["Action", "Narrative"]
    result = _shortcut(value, Path("/home/person/.local/bin/riftlift"))
    assert result["icon"] == "/metadata/example/icon.png"
    assert list(result["tags"].values()) == ["VR", "RiftLift", "Action", "Narrative"]


def test_wayvr_metadata_is_not_created_when_wayvr_is_absent(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr("riftlift.steam.shutil.which", lambda _name: None)

    _install_wayvr_metadata(game(), 123)

    assert not (tmp_path / "wayvr").exists()


def test_native_steam_games_do_not_create_duplicate_shortcuts(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    rift = game()
    steam = Game(
        "steam-game",
        "Steam Game",
        "456",
        "steam.app.456",
        "/games/steam",
        "game.exe",
        [],
        source="steam",
    )
    rift.save(paths)
    steam.save(paths)

    assert _shortcut_games(paths) == [rift]


def test_local_game_with_steam_style_app_key_still_gets_a_shortcut(
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
    local = Game(
        "local-game",
        "Local Game",
        "",
        "steam.app.custom",
        "/games/local",
        "game.exe",
        [],
        source="local",
    )
    local.save(paths)

    assert _shortcut_games(paths) == [local]


def test_process_discovery_ignores_other_users(tmp_path: Path, monkeypatch) -> None:
    mine = tmp_path / "100"
    other = tmp_path / "200"
    mine.mkdir()
    other.mkdir()
    (mine / "comm").write_text("not-steam\n")
    (mine / "status").write_text("Uid:\t1000\t1000\t1000\t1000\n")
    (other / "comm").write_text("steam\n")
    (other / "status").write_text("Uid:\t2000\t2000\t2000\t2000\n")
    monkeypatch.setattr("riftlift.steam.os.getuid", lambda: 1000)

    assert not _same_user_process_running({"steam"}, tmp_path)


def test_process_discovery_finds_current_user(tmp_path: Path, monkeypatch) -> None:
    process = tmp_path / "100"
    process.mkdir()
    (process / "comm").write_text("steamwebhelper\n")
    (process / "status").write_text("Uid:\t1000\t1000\t1000\t1000\n")
    monkeypatch.setattr("riftlift.steam.os.getuid", lambda: 1000)

    assert _same_user_process_running({"steam", "steamwebhelper"}, tmp_path)


def test_steam_client_is_started_before_a_steamworks_game(monkeypatch) -> None:
    readiness = iter((False, False, True))
    popen_calls = []
    monkeypatch.setattr("riftlift.steam._steam_client_ready", lambda: next(readiness))
    monkeypatch.setattr("riftlift.steam.shutil.which", lambda _name: "/usr/bin/steam")
    monkeypatch.setattr(
        "riftlift.steam.subprocess.Popen",
        lambda command, **kwargs: popen_calls.append((command, kwargs)),
    )
    monkeypatch.setattr("riftlift.steam.time.sleep", lambda _seconds: None)

    ensure_steam_running()

    assert popen_calls[0][0] == ("/usr/bin/steam", "-silent")
    assert popen_calls[0][1]["start_new_session"] is True


def test_missing_steam_launcher_has_actionable_error(monkeypatch) -> None:
    monkeypatch.setattr("riftlift.steam._steam_client_ready", lambda: False)
    monkeypatch.setattr("riftlift.steam.shutil.which", lambda _name: None)

    with pytest.raises(RiftLiftError, match="start Steam and retry"):
        ensure_steam_running()


def test_sync_failure_restarts_the_previously_running_client(
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
    running = iter((True, False, False))
    starts = []
    monkeypatch.setattr("riftlift.steam._steam_running", lambda: next(running))
    monkeypatch.setattr("riftlift.steam.shutil.which", lambda _name: "/usr/bin/steam")
    monkeypatch.setattr("riftlift.steam.subprocess.run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "riftlift.steam.sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RiftLiftError("bad VDF")),
    )
    monkeypatch.setattr(
        "riftlift.steam._start_steam", lambda executable: starts.append(executable)
    )

    with pytest.raises(RiftLiftError, match="bad VDF"):
        sync_with_restart(paths)

    assert starts == ["/usr/bin/steam"]


def test_user_config_uses_steams_latest_signed_in_account(tmp_path: Path) -> None:
    older = tmp_path / "userdata/123/config"
    current = tmp_path / "userdata/456/config"
    older.mkdir(parents=True)
    current.mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/loginusers.vdf").write_text(
        '"users" { '
        '"76561197960265851" { "Timestamp" "20" } '
        '"76561197960266184" { "Timestamp" "30" } '
        "}"
    )

    assert user_config(tmp_path) == current


def test_user_config_refuses_to_guess_between_accounts(tmp_path: Path) -> None:
    (tmp_path / "userdata/123/config").mkdir(parents=True)
    (tmp_path / "userdata/456/config").mkdir(parents=True)

    with pytest.raises(RiftLiftError, match="multiple local profiles"):
        user_config(tmp_path)


def test_installed_command_honors_xdg_bin_home(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "custom-bin/riftlift"
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setenv("XDG_BIN_HOME", str(executable.parent))
    monkeypatch.setattr("riftlift.util.shutil.which", lambda _name: None)

    assert installed_command("riftlift") == executable


def test_installed_command_treats_empty_bin_home_as_unset(
    tmp_path, monkeypatch
) -> None:
    executable = tmp_path / ".local/bin/riftlift"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_BIN_HOME", "")
    monkeypatch.setattr("riftlift.util.shutil.which", lambda _name: None)

    assert installed_command("riftlift") == executable
