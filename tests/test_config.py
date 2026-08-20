from pathlib import Path

import pytest

from riftlift.config import (
    Game,
    Paths,
    debug_logging_enabled,
    games,
    set_debug_logging,
    xdg_data_dirs,
)


def test_game_roundtrip(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    game = Game(
        "example", "Example", "123", "example-key", str(tmp_path), "game.exe", ["-vr"]
    )
    game.save(paths)
    assert Game.load(paths, "example") == game


def test_default_paths_treat_empty_xdg_values_as_unset(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", "")
    monkeypatch.setenv("XDG_CACHE_HOME", "")
    monkeypatch.setenv("XDG_CONFIG_HOME", "")

    paths = Paths.defaults()

    assert paths.data == tmp_path / ".local/share/riftlift"
    assert paths.cache == tmp_path / ".cache/riftlift"
    assert paths.config == tmp_path / ".config/riftlift"


def test_default_paths_ignore_relative_xdg_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", "relative-data")
    monkeypatch.setenv("XDG_CACHE_HOME", "relative-cache")
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative-config")

    paths = Paths.defaults()

    assert paths.data == tmp_path / ".local/share/riftlift"
    assert paths.cache == tmp_path / ".cache/riftlift"
    assert paths.config == tmp_path / ".config/riftlift"


def test_xdg_data_dirs_ignore_relative_entries(monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_DIRS", "/opt/share:relative:/usr/share")

    assert xdg_data_dirs() == (Path("/opt/share"), Path("/usr/share"))


def test_game_records_reject_unsafe_slugs_and_non_objects(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    with pytest.raises(ValueError, match="invalid game slug"):
        Game.load(paths, "../outside")

    records = paths.data / "games"
    records.mkdir(parents=True)
    (records / "broken.json").write_text("[]")
    with pytest.raises(ValueError, match="not a JSON object"):
        Game.load(paths, "broken")


def test_game_records_do_not_silently_escape_or_disappear(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    records = paths.data / "games"
    records.mkdir(parents=True)
    (records / "broken.json").write_text(
        """{
          "slug": "broken",
          "name": "Broken",
          "app_id": "1",
          "app_key": "broken",
          "directory": "/games/broken",
          "executable": "../outside.exe",
          "arguments": []
        }"""
    )

    with pytest.raises(ValueError, match="must stay inside"):
        Game.load(paths, "broken")

    with pytest.raises(ValueError, match="invalid game record"):
        games(paths)


def test_game_records_reject_unknown_fields(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    game = Game(
        "example", "Example", "123", "example-key", str(tmp_path), "game.exe", []
    )
    target = game.save(paths)
    payload = target.read_text().replace(
        '"source": "meta"', '"source": "meta",\n  "launch_argumants": []'
    )
    target.write_text(payload)

    with pytest.raises(ValueError, match=r"unknown fields.*launch_argumants"):
        Game.load(paths, "example")


def test_debug_logging_setting_is_private_and_persistent(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )

    set_debug_logging(paths, True)

    marker = paths.config / "debug-logging"
    assert debug_logging_enabled(paths)
    assert marker.stat().st_mode & 0o777 == 0o600
    set_debug_logging(paths, False)
    assert not debug_logging_enabled(paths)
