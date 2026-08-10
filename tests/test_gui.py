import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from riftlift.cli import parser
from riftlift.config import Paths
from riftlift.gui_qt import Window


def test_gui_command_is_available() -> None:
    assert parser().parse_args(["gui"]).command == "gui"


def test_gui_exposes_only_the_primary_library_actions(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    buttons = {button.text() for button in window.findChildren(QtWidgets.QPushButton)}
    assert {
        "Check system",
        "Sign in",
        "Add game",
        "Refresh library",
        "View activity",
    } <= buttons
    assert not window.findChildren(QtWidgets.QSpinBox)
    assert "Your Meta Rift library, lifted into Linux OpenXR" not in {
        label.text() for label in window.findChildren(QtWidgets.QLabel)
    }

    window.close()
    app.processEvents()
