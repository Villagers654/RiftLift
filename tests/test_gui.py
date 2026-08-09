from riftlift.cli import parser


def test_gui_command_is_available() -> None:
    assert parser().parse_args(["gui"]).command == "gui"
