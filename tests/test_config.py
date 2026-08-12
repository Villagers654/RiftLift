from pathlib import Path

from riftlift.config import (
    Game,
    Paths,
    debug_logging_enabled,
    set_debug_logging,
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
