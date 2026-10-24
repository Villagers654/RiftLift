"""The Windows command line's version listing and every-version install."""

from meta_pcvr_downloader.api import Build

from riftlift import windows
from riftlift.builds import AvailableBuild
from riftlift.config import Game, Paths
from riftlift.library import NotLaunchableError


def _paths(tmp_path) -> Paths:
    return Paths(
        *(
            tmp_path / name
            for name in ("data", "cache", "config", "games", "prefix", "tools")
        )
    )


def _run(tmp_path, *argv) -> int:
    return windows._run_command(_paths(tmp_path), windows.parser().parse_args(argv))


def test_windows_lists_every_build(tmp_path, monkeypatch, capsys) -> None:
    builds = [
        AvailableBuild("1", "Echo VR", "b2", "34.4", 2206, channels=("LIVE",)),
        AvailableBuild("1", "Echo VR", "b1", "34.3", 2202),
    ]
    monkeypatch.setattr("riftlift.library.available_builds", lambda *_args: builds)

    assert _run(tmp_path, "builds", "1") == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Echo VR: 2 downloadable version(s)"
    assert lines[2].split() == ["34.4", "2206", "b2", "LIVE"]
    assert lines[3].split() == ["34.3", "2202", "b1", "-"]


def test_windows_installs_every_version(tmp_path, monkeypatch, capsys) -> None:
    game = Game("echo-vr", "Echo VR", "1", "k", str(tmp_path), "e.exe", [])
    old = Build("1", "Echo VR", "b0", "1.0", 1)
    seen = []

    def add_all_versions(paths, app, *, jobs):
        seen.append((app, jobs))
        return [game], [(old, NotLaunchableError(paths.games / "old", ValueError()))]

    def single_add(*_args, **_kwargs):
        raise AssertionError("'all' is not a build to pass to add()")

    monkeypatch.setattr("riftlift.library.add_all_versions", add_all_versions)
    monkeypatch.setattr("riftlift.library.add", single_add)

    assert _run(tmp_path, "add", "1", "--build", "all", "--jobs", "3") == 1

    assert seen == [("1", 3)]
    out = capsys.readouterr().out
    assert "Installed 1 version(s); 1 downloaded but not launchable; 0 failed." in out
    assert "1.0 (1): downloaded to" in out
