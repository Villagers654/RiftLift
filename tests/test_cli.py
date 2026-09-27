from pathlib import Path

import pytest

from riftlift.cli import main
from riftlift.config import Game, Paths


@pytest.mark.parametrize("command", ("launch", "launch-steam"))
def test_help_after_game_identifier_never_launches(command, monkeypatch, capsys):
    monkeypatch.setattr(
        "riftlift.cli.launch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("help launched a game")
        ),
    )

    with pytest.raises(SystemExit) as stopped:
        main([command, "sample", "--help"])

    assert stopped.value.code == 0
    assert f"usage: riftlift {command}" in capsys.readouterr().out


def test_remove_command_deletes_the_game_and_syncs_steam(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    data = tmp_path / "data"
    paths = Paths(
        data,
        tmp_path / "cache",
        tmp_path / "config",
        data / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    game_dir = tmp_path / "games/aircar"
    game_dir.mkdir(parents=True)
    (game_dir / "Aircar.exe").touch()
    Game("aircar", "Aircar", "1", "meta.aircar", str(game_dir), "Aircar.exe", []).save(
        paths
    )
    monkeypatch.setattr("riftlift.cli.Paths.defaults", staticmethod(lambda: paths))
    monkeypatch.setattr("riftlift.cli.sync_with_restart", lambda _paths: "synced")

    assert main(["remove", "aircar"]) == 0

    assert not game_dir.exists()
    output = capsys.readouterr().out
    assert "Removed Aircar." in output
    assert "Updated Steam (synced)." in output
