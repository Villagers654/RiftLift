import platform

from riftlift.cli import parser
from riftlift.gui_qt import LINUX


def test_gui_command_is_available() -> None:
    assert parser().parse_args(["gui"]).command == "gui"


def test_platform_mode_matches_host() -> None:
    assert LINUX == (platform.system() == "Linux")
