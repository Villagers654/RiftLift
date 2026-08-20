from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .util import atomic_write_text

_GAME_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _game_record(paths: Paths, slug: str) -> Path:
    if _GAME_SLUG.fullmatch(slug) is None:
        raise ValueError(f"invalid game slug: {slug!r}")
    return paths.data / "games" / f"{slug}.json"


def _xdg(name: str, fallback: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else fallback


def xdg_data_home() -> Path:
    """Return the freedesktop user data directory."""
    return _xdg("XDG_DATA_HOME", Path.home() / ".local/share")


def xdg_config_home() -> Path:
    """Return the freedesktop user configuration directory."""
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config")


@dataclass(slots=True)
class Paths:
    data: Path
    cache: Path
    config: Path
    games: Path
    prefix: Path
    tools: Path

    @classmethod
    def defaults(cls) -> Paths:
        home = Path.home()
        data = xdg_data_home() / "riftlift"
        cache = _xdg("XDG_CACHE_HOME", home / ".cache") / "riftlift"
        config = xdg_config_home() / "riftlift"
        games = Path(os.environ.get("RIFTLIFT_GAMES_DIR", home / "Games/RiftLift"))
        return cls(data, cache, config, games, data / "compatdata", data / "tools")

    def create(self) -> None:
        for path in (
            self.data,
            self.cache,
            self.config,
            self.games,
            self.prefix,
            self.tools,
        ):
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
    platform_offline: bool = False
    store_url: str = ""
    description: str = ""
    developer: str = ""
    publisher: str = ""
    genres: list[str] = field(default_factory=list)
    artwork: dict[str, str] = field(default_factory=dict)
    steam_app_id: int = 0
    source: str = "meta"

    @property
    def game_dir(self) -> Path:
        return Path(self.directory)

    @property
    def executable_path(self) -> Path:
        return self.game_dir / self.executable

    def save(self, paths: Paths) -> Path:
        paths.create()
        target = _game_record(paths, self.slug)
        atomic_write_text(target, json.dumps(asdict(self), indent=2) + "\n")
        return target

    @classmethod
    def load(cls, paths: Paths, slug: str) -> Game:
        target = _game_record(paths, slug)
        try:
            value: Any = json.loads(target.read_text())
        except FileNotFoundError as error:
            raise ValueError(
                f"unknown game {slug!r}; add it to RiftLift first"
            ) from error
        if not isinstance(value, dict):
            raise ValueError(f"game record is not a JSON object: {target}")
        if "source" not in value:
            value["source"] = (
                "steam"
                if str(value.get("app_key", "")).startswith("steam.app.")
                else "meta"
            )
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


def debug_logging_enabled(paths: Paths) -> bool:
    return (paths.config / "debug-logging").is_file()


def set_debug_logging(paths: Paths, enabled: bool) -> None:
    target = paths.config / "debug-logging"
    if not enabled:
        target.unlink(missing_ok=True)
        return
    paths.config.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("1\n")
    target.chmod(0o600)
