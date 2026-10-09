import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_wheel_ships_the_ui_assets():
    # Linux installs (wheel, AppImage) load the brand and folder icons from the package.
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section = re.search(
        r"^\[tool\.setuptools\.package-data\]\n(.*?)(?=^\[|\Z)", project, re.M | re.S
    )
    assert section and re.search(
        r'^riftlift = \[.*"assets/\*\.svg".*\]', section.group(1), re.M
    )
    assert sorted((ROOT / "src/riftlift/assets").glob("*.svg"))
