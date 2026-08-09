from pathlib import Path

from riftlift.config import Game
from riftlift.steam import _existing_by_slug, _shortcut


def game() -> Game:
    return Game("example", "Example VR", "123", "example", "/games/example", "game.exe", [])


def test_existing_shortcut_id_is_preserved() -> None:
    existing = {
        "0": {
            "appid": 4214331913,
            "LaunchOptions": "launch example",
            "tags": {"0": "VR", "1": "RiftLift"},
        }
    }
    prior = _existing_by_slug(existing)["example"]
    result = _shortcut(game(), Path("/home/person/.local/bin/riftlift"), prior["appid"])
    assert result["appid"] == 4214331913


def test_shortcut_uses_catalog_icon() -> None:
    value = game()
    value.artwork = {"icon": "/metadata/example/icon.png"}
    value.genres = ["Action", "Narrative"]
    result = _shortcut(value, Path("/home/person/.local/bin/riftlift"))
    assert result["icon"] == "/metadata/example/icon.png"
    assert list(result["tags"].values()) == ["VR", "RiftLift", "Action", "Narrative"]
