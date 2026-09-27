import json
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
        "example",
        "Example",
        "123",
        "example-key",
        str(tmp_path),
        "game.exe",
        ["-vr"],
        launch_options=["--mods", "path with spaces"],
        dll_overrides="version=n,b",
        environment={"PROTON_LOG": "1", "CUSTOM": "a=b c"},
    )
    game.save(paths)
    assert Game.load(paths, "example") == game


@pytest.mark.parametrize(
    "environment", [{"BAD=NAME": "1"}, {"VAR": 1}, {"VAR": "a\0b"}, {"VAR": "a\nb"}, []]
)
def test_game_rejects_invalid_environment(tmp_path, environment):
    with pytest.raises(ValueError, match="Environment variables"):
        Game(
            "test",
            "Test",
            "1",
            "key",
            str(tmp_path),
            "Game.exe",
            [],
            environment=environment,
        )


def test_game_records_saved_before_description_lang_are_treated_as_unknown(
    tmp_path: Path,
) -> None:
    # A record saved before this field existed could have been fetched in
    # any language (e.g. via IP-geolocated content, not an explicit
    # Accept-Language header), so it must never be assumed to already match
    # the current UI language - that would silently skip a needed refresh.
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    game = Game(
        "example",
        "Example",
        "123",
        "example-key",
        str(tmp_path),
        "game.exe",
        [],
        description="A game.",
    )
    target = game.save(paths)
    payload = json.loads(target.read_text())
    del payload["description_lang"]
    target.write_text(json.dumps(payload))

    assert Game.load(paths, "example").description_lang == ""


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


def test_game_records_ignore_unknown_fields(tmp_path: Path) -> None:
    # A record can carry a field from a different build (an experimental
    # branch, a downgrade) - it should still load instead of crashing the
    # whole library over one game.
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
        '"source": "meta"', '"source": "meta",\n  "unity_xr_plugin": "openxr"'
    )
    target.write_text(payload)

    assert Game.load(paths, "example") == game


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
