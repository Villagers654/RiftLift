"""Steam visibility is based on the active profile, including stale saved IDs."""

import uuid
from pathlib import Path

import pytest

from riftlift.config import Game
from riftlift.steam import shortcut_state
from riftlift.steam_vdf import dumps
from riftlift.util import RiftLiftError


@pytest.mark.parametrize("state", ["present", "absent", "corrupt", "missing_profile"])
def test_actual_shortcut_state(monkeypatch, state):
    config = Path(__file__).resolve().parents[3] / "review-fixtures" / str(uuid.uuid4())
    config.mkdir(parents=True)
    game = Game(
        "fixture",
        "Fixture",
        "1",
        "fixture",
        str(config),
        "fixture.exe",
        [],
        steam_app_id=42,
    )
    if state == "missing_profile":

        def fail():
            raise RiftLiftError("fixture unavailable")

        monkeypatch.setattr("riftlift.steam.user_config", fail)
    else:
        monkeypatch.setattr("riftlift.steam.user_config", lambda: config)
    if state == "present":
        (config / "shortcuts.vdf").write_bytes(
            dumps(
                {
                    "shortcuts": {
                        "0": {
                            "tags": {"0": "RiftLift"},
                            "LaunchOptions": "launch fixture",
                            "appid": 42,
                        }
                    }
                }
            )
        )
    elif state == "corrupt":
        (config / "shortcuts.vdf").write_bytes(b"invalid fixture")
    expected = state if state in {"present", "absent"} else "unavailable"
    assert shortcut_state(game) == expected
