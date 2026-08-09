from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


def _xdg(name: str, fallback: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else fallback


@dataclass(slots=True)
class Paths:
    data: Path
    cache: Path
    config: Path
    games: Path
    prefix: Path
    tools: Path

    @classmethod
    def defaults(cls) -> "Paths":
        home = Path.home()
        data = _xdg("XDG_DATA_HOME", home / ".local/share") / "riftlift"
        cache = _xdg("XDG_CACHE_HOME", home / ".cache") / "riftlift"
        config = _xdg("XDG_CONFIG_HOME", home / ".config") / "riftlift"
        games = Path(os.environ.get("RIFTLIFT_GAMES_DIR", home / "Games/RiftLift"))
        return cls(data, cache, config, games, data / "compatdata", data / "tools")

    def create(self) -> None:
        for path in (self.data, self.cache, self.config, self.games, self.prefix, self.tools):
            path.mkdir(parents=True, exist_ok=True)
        (self.data / "games").mkdir(exist_ok=True)


@dataclass(slots=True)
class Game:
    slug: str
    name: str
    app_id: str
    app_key: str
    directory: str
    executable: str
    arguments: list[str]
    version: str = ""
    platform_shim: bool = True

    @property
    def game_dir(self) -> Path:
        return Path(self.directory)

    @property
    def executable_path(self) -> Path:
        return self.game_dir / self.executable

    def save(self, paths: Paths) -> Path:
        paths.create()
        target = paths.data / "games" / f"{self.slug}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2) + "\n")
        temporary.replace(target)
        return target

    @classmethod
    def load(cls, paths: Paths, slug: str) -> "Game":
        target = paths.data / "games" / f"{slug}.json"
        try:
            value: dict[str, Any] = json.loads(target.read_text())
        except FileNotFoundError as error:
            raise ValueError(f"unknown game {slug!r}; run 'riftlift add STORE_URL' first") from error
        allowed = {field.name for field in fields(cls)}
        return cls(**{key: item for key, item in value.items() if key in allowed})


def games(paths: Paths) -> list[Game]:
    result: list[Game] = []
    for target in sorted((paths.data / "games").glob("*.json")):
        try:
            result.append(Game.load(paths, target.stem))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return result

