from pathlib import Path

from riftlift.config import Paths
from riftlift.playtime import (
    Playtime,
    PlaytimeSession,
    add_playtime,
    format_playtime,
    mark_launch,
    playtime,
    playtime_label,
)


def paths_at(tmp_path: Path) -> Paths:
    return Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )


def test_playtime_accumulates_across_launches(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    mark_launch(paths, "echo", "2026-08-11T12:00:00+00:00")
    add_playtime(paths, "echo", 65.5)
    mark_launch(paths, "echo", "2026-08-11T13:00:00+00:00")
    add_playtime(paths, "echo", 3600)

    tracked = playtime(paths, "echo")
    assert tracked.seconds == 3665.5
    assert tracked.launches == 2
    assert tracked.last_played_at == "2026-08-11T13:00:00+00:00"
    assert (paths.data / "playtime.json").stat().st_mode & 0o777 == 0o600


def test_session_records_exact_final_interval(tmp_path: Path) -> None:
    paths = paths_at(tmp_path)
    ticks = iter((100.0, 125.25, 140.25))
    session = PlaytimeSession(
        paths, "vader", clock=lambda: next(ticks), background=False
    )
    session.checkpoint()
    session.close()

    tracked = playtime(paths, "vader")
    assert tracked.seconds == 40.25
    assert tracked.launches == 1


def test_playtime_display_is_compact() -> None:
    assert format_playtime(0) == "< 1m"
    assert format_playtime(3599) == "59m"
    assert format_playtime(3600) == "1h"
    assert format_playtime(7380) == "2h 3m"
    assert playtime_label(Playtime()) == "Not played yet"
