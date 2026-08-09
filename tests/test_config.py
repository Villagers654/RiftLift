from pathlib import Path

from riftlift.config import Game, Paths


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
