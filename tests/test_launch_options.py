import json
import os
from argparse import Namespace
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from riftlift.cli import _run_steam_launch
from riftlift.config import Game, Paths
from riftlift.game_ui import LaunchOptionsDialog
from riftlift.main_window import Window


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def paths(tmp_path):
    return Paths(
        *(
            tmp_path / name
            for name in ("data", "cache", "config", "games", "prefix", "tools")
        )
    )


@pytest.fixture
def game(tmp_path):
    (tmp_path / "Game.exe").touch()
    return Game(
        "sample",
        "Sample",
        "123",
        "steam.app.123",
        str(tmp_path),
        "Game.exe",
        ["--default"],
        launch_options=["--mods", "folder with spaces"],
        dll_overrides="version=n,b",
        environment={"PROTON_LOG": "1", "CUSTOM": "a=b c", "EMPTY": ""},
    )


def test_dialog_roundtrip_and_clear(app, game):
    dialog = LaunchOptionsDialog(game)
    dialog._save()
    assert dialog.result() == QtWidgets.QDialog.Accepted
    assert dialog.updated_game == game
    dialog.close()

    dialog = LaunchOptionsDialog(game)
    dialog.arguments_entry.clear()
    dialog.overrides_entry.clear()
    dialog.environment_entry.clear()
    dialog._save()
    assert dialog.updated_game.launch_options == []
    assert dialog.updated_game.dll_overrides == ""
    assert dialog.updated_game.environment == {}
    assert dialog.updated_game.arguments == ["--default"]
    assert game.environment["PROTON_LOG"] == "1"
    dialog.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("arguments_entry", '"unclosed'),
        ("overrides_entry", "version=oops"),
        ("environment_entry", "export PROTON_LOG=1"),
        ("environment_entry", "PROTON_LOG"),
    ],
)
def test_invalid_settings_do_not_accept_or_mutate_game(app, game, field, value):
    dialog = LaunchOptionsDialog(game)
    entry = getattr(dialog, field)
    if isinstance(entry, QtWidgets.QPlainTextEdit):
        entry.setPlainText(value)
    else:
        entry.setText(value)
    dialog._save()
    assert dialog.result() != QtWidgets.QDialog.Accepted
    assert dialog.updated_game is None
    assert dialog.error.text()
    assert game.dll_overrides == "version=n,b"
    dialog.close()


def test_window_saves_settings_and_cancel_preserves_them(app, paths, game, monkeypatch):
    game.save(paths)
    window = Window(paths)
    window.show_game(game)

    def accept(dialog):
        dialog.arguments_entry.setText('--custom "two words"')
        dialog.overrides_entry.setText("winhttp=n,b")
        dialog.environment_entry.setPlainText("PROTON_LOG=0\nCUSTOM=literal $(text)")
        dialog._save()
        return dialog.result()

    monkeypatch.setattr(LaunchOptionsDialog, "exec", accept)
    window.launch_options()
    saved = Game.load(paths, game.slug)
    assert saved.launch_options == ["--custom", "two words"]
    assert saved.dll_overrides == "winhttp=n,b"
    assert saved.environment == {"PROTON_LOG": "0", "CUSTOM": "literal $(text)"}
    assert window.game() == saved

    monkeypatch.setattr(
        LaunchOptionsDialog, "exec", lambda _self: QtWidgets.QDialog.Rejected
    )
    window.launch_options()
    assert Game.load(paths, game.slug) == saved
    window.close()


def test_steam_command_keeps_saved_settings(paths, game, monkeypatch):
    game.save(paths)
    discovered = replace(game, launch_options=[], dll_overrides="", environment={})
    monkeypatch.setattr("riftlift.cli.steam_oculus_game", lambda _id: discovered)
    launched = []
    monkeypatch.setattr(
        "riftlift.cli.launch", lambda _paths, game, _args: launched.append(game) or 0
    )
    assert (
        _run_steam_launch(
            paths,
            Namespace(
                app_id="123", steam_command=[str(game.executable_path), "--steam"]
            ),
        )
        == 0
    )
    assert launched[0].arguments == ["--steam"]
    assert launched[0].launch_options == game.launch_options
    assert launched[0].dll_overrides == game.dll_overrides
    assert launched[0].environment == game.environment


def test_old_records_get_empty_settings(paths, game):
    record = game.save(paths)
    data = json.loads(record.read_text())
    for key in ("launch_options", "dll_overrides", "environment"):
        data.pop(key)
    record.write_text(json.dumps(data))
    loaded = Game.load(paths, game.slug)
    assert loaded.launch_options == []
    assert loaded.dll_overrides == ""
    assert loaded.environment == {}
