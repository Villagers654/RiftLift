import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from riftlift.config import Game, Paths
from riftlift.steam_ui import SteamGamesDialog


def paths(tmp_path: Path) -> Paths:
    return Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )


def steam_game() -> Game:
    return Game(
        "stereopaint",
        "StereoPaint",
        "1920760",
        "steam.app.1920760",
        "/steam/StereoPaint",
        "StereoPaint.exe",
        [],
    )


def test_selects_discovered_game_for_import(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "riftlift.steam_ui.QtCore.QTimer.singleShot", lambda *_args: None
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = SteamGamesDialog(paths(tmp_path))
    game = steam_game()

    dialog.finish_scan([game], None)

    assert dialog.list.count() == 1
    assert dialog.add_button.text() == "Add to RiftLift"
    assert dialog.add_button.isEnabled()
    dialog.accept_selected()
    assert dialog.result() == QtWidgets.QDialog.Accepted
    assert dialog.selected_game is game
    dialog.close()
    app.processEvents()


def test_marks_game_that_is_already_in_library(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "riftlift.steam_ui.QtCore.QTimer.singleShot", lambda *_args: None
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    test_paths = paths(tmp_path)
    game = steam_game()
    game.save(test_paths)
    dialog = SteamGamesDialog(test_paths)

    dialog.finish_scan([game], None)

    assert "already in RiftLift" in dialog.list.item(0).text()
    assert dialog.add_button.text() == "Refresh in RiftLift"
    dialog.close()
    app.processEvents()
