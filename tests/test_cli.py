import pytest

from riftlift.cli import main


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
