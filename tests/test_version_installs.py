from pathlib import Path

import pytest
from meta_pcvr_downloader.api import Build

from riftlift.builds import AvailableBuild, merge_builds
from riftlift.config import Paths


def _paths(tmp_path: Path) -> Paths:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    return paths


def _pe64(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray(0x200)
    header[:2] = b"MZ"
    header[0x3C:0x40] = (0x80).to_bytes(4, "little")
    header[0x80:0x84] = b"PE\0\0"
    header[0x84:0x86] = (0x8664).to_bytes(2, "little")
    path.write_bytes(bytes(header))


def test_the_default_install_stays_on_the_build_meta_offers() -> None:
    from riftlift.builds import default_build

    offered = Build("1", "Bigscreen", "live", "0.950.2.03f010", 3451)
    history = [
        AvailableBuild(
            "1", "Bigscreen", "alpha", "0.950.2.a276a2", 3452, channels=("PCALPHA",)
        ),
        AvailableBuild(
            "1", "Bigscreen", "live", "0.950.2.03f010", 3451, channels=("LIVE",)
        ),
    ]

    builds = merge_builds([offered], history)

    # The alpha build is newer and stays installable, but only when chosen.
    assert [build.binary_id for build in builds] == ["alpha", "live"]
    assert default_build(builds).binary_id == "live"
    assert default_build([offered]).binary_id == "live"


def test_a_plain_install_downloads_the_offered_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from riftlift import library

    paths = _paths(tmp_path)
    builds = merge_builds(
        [Build("1", "Game", "live", "1.0", 10)],
        [AvailableBuild("1", "Game", "beta", "1.1", 11, channels=("BETA",))],
    )
    downloaded = []
    monkeypatch.setattr(library, "account_tokens", lambda *_args: ["token"])
    monkeypatch.setattr(
        library, "fetch_manifest", lambda _t, build: {"launchFile": "a.exe"}
    )
    monkeypatch.setattr(library, "populate_game_metadata", lambda *_args: None)

    class Downloader:
        def __init__(self, _token, build, directory, _cache, _workers):
            downloaded.append(build.binary_id)
            self.directory = directory

        def run(self, _manifest):
            _pe64(self.directory / "a.exe")

    monkeypatch.setattr(library, "Downloader", Downloader)

    game = library.add(paths, "1", builds=builds)

    assert downloaded == ["live"]
    assert game.binary_id == "live"


@pytest.fixture
def app():
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_the_picker_offers_the_default_build_first(app, tmp_path: Path) -> None:
    from riftlift.game_ui import StoreGameDialog

    dialog = StoreGameDialog(_paths(tmp_path), lambda: None)
    dialog._set_builds(
        merge_builds(
            [Build("1", "Game", "live", "1.0", 10)],
            [AvailableBuild("1", "Game", "beta", "1.1", 11, channels=("BETA",))],
        )
    )

    items = [
        (dialog.versions.itemText(i), dialog.versions.itemData(i))
        for i in range(dialog.versions.count())
    ]
    assert items == [
        ("1.0 (10) (latest)", None),
        ("1.1 (11) · BETA", "beta"),
        ("All versions (2)", "all"),
    ]
    dialog.close()


def _record(paths: Path, slug: str, version: str, binary_id: str) -> None:
    from riftlift.config import Game

    Game(
        slug,
        f"Game ({version})",
        "1",
        "k",
        str(paths.games / slug),
        "a.exe",
        [],
        version=version,
        source="meta",
        binary_id=binary_id,
    ).save(paths)


def test_a_chosen_version_keeps_out_of_another_builds_paused_folder(
    tmp_path: Path,
) -> None:
    from riftlift.library import BUILD_MARKER, _install_identity

    paths = _paths(tmp_path)
    paused = paths.games / "game"
    paused.mkdir(parents=True)
    (paused / BUILD_MARKER).write_text("other\n")
    chosen = Build("1", "Game", "chosen", "2.0", 20)

    assert _install_identity(paths, chosen, side_by_side=True) == (
        "game-2-0",
        "Game (2.0)",
    )
    # The paused build itself still resumes into its folder.
    paused_build = Build("1", "Game", "other", "1.0", 10)
    assert _install_identity(paths, paused_build, side_by_side=True)[0] == "game"


def test_the_build_code_fallback_never_lands_on_another_version(tmp_path: Path) -> None:
    from riftlift.library import _install_identity

    paths = _paths(tmp_path)
    _record(paths, "game", "3.0", "b30")
    _record(paths, "game-1-0-2", "1.0.2", "b102")
    _record(paths, "game-1-0", "1.0", "b10")
    # "1.0" with build code 2 would fall back to game-1-0-2, version 1.0.2's folder.
    second = Build("1", "Game", "b2", "1.0", 2)

    slug, name = _install_identity(paths, second, side_by_side=True)

    assert slug not in {"game", "game-1-0", "game-1-0-2"}
    assert name == "Game (1.0, build 2)"
    assert _install_identity(paths, second, side_by_side=True)[0] == slug


def test_every_version_leaves_installed_versions_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from riftlift import library
    from riftlift.config import Game

    paths = _paths(tmp_path)
    (paths.games / "game").mkdir(parents=True)
    installed = Game(
        "game",
        "Game",
        "1",
        "k",
        str(paths.games / "game"),
        "a.exe",
        ["-custom"],
        version="2.0",
        source="meta",
        binary_id="b2",
        environment={"KEY": "value"},
    )
    installed.save(paths)
    builds = [Build("1", "Game", "b2", "2.0", 2), Build("1", "Game", "b1", "1.0", 1)]
    downloaded = []
    monkeypatch.setattr(library, "account_tokens", lambda *_args: ["token"])
    monkeypatch.setattr(
        library, "fetch_manifest", lambda _t, _b: {"launchFile": "a.exe"}
    )
    monkeypatch.setattr(library, "populate_game_metadata", lambda *_args: None)

    class Downloader:
        def __init__(self, _token, build, directory, _cache, _workers):
            downloaded.append(build.binary_id)
            self.directory = directory

        def run(self, _manifest):
            _pe64(self.directory / "a.exe")

    monkeypatch.setattr(library, "Downloader", Downloader)

    games, failed = library.add_all_versions(paths, "1", builds=builds)

    assert not failed
    assert downloaded == ["b1"]
    assert [game.slug for game in games] == ["game", "game-1-0"]
    kept = Game.load(paths, "game")
    assert kept.arguments == ["-custom"]
    assert kept.environment == {"KEY": "value"}


def test_an_expired_sign_in_asks_to_sign_in_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from meta_pcvr_downloader.api import MetaApiError

    from riftlift import game_ui
    from riftlift.util import RiftLiftError

    def expired(*_args):
        raise MetaApiError(
            'Meta API returned HTTP 400: {"error":{"message":"Error validating access '
            'token","type":"OAuthException","code":190}}'
        )

    def delisted(_app_id):
        raise RiftLiftError("Meta's store page has no catalog metadata for app 1")

    monkeypatch.setattr(game_ui, "_is_signed_in", lambda _paths: True)
    monkeypatch.setattr(game_ui, "available_builds", expired)
    monkeypatch.setattr(game_ui, "fetch_catalog_metadata", delisted)

    with pytest.raises(RiftLiftError) as raised:
        game_ui.check_store_link(_paths(tmp_path), "1")

    assert str(raised.value) == game_ui.ADD_GAME("sign_in_expired")


def _run_worker(monkeypatch, paths: Paths, build: str):
    import io
    import json
    import sys

    from riftlift import download_worker

    request = {
        "paths": {
            key: str(getattr(paths, key))
            for key in ("data", "cache", "config", "games", "prefix", "tools")
        },
        "url": "1",
        "sync_steam": False,
        "build": build,
    }
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request) + "\n"))
    monkeypatch.setattr(sys, "stdout", output)
    download_worker.main()
    return [json.loads(line) for line in output.getvalue().splitlines()]


def test_a_build_that_cannot_launch_is_not_reported_as_a_network_error(
    app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from riftlift.download_job import DownloadJob
    from riftlift.library import NotLaunchableError

    paths = _paths(tmp_path)

    def add(paths, url, *, build_selector, on_finalizing):
        raise NotLaunchableError(paths.games / "game-1-0", ValueError("no 64-bit exe"))

    monkeypatch.setattr("riftlift.library.add", add)
    events = _run_worker(monkeypatch, paths, "b1")
    assert events[-1]["event"] == "error"
    assert events[-1]["reason"] == "not_launchable"
    assert "game-1-0" in events[-1]["detail"]

    job = DownloadJob(paths, "1", False, build_selector="b1")
    job._handle(events[-1])
    assert job._error == "not_launchable"


def test_failed_versions_are_listed_even_when_none_installed(
    app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from PySide6 import QtWidgets

    from riftlift import game_ui

    notices = []
    monkeypatch.setattr(
        game_ui,
        "_themed_notice",
        lambda _parent, _title, text, details="": notices.append(
            f"{text}\n\n{details}"
        ),
    )
    window = QtWidgets.QWidget()
    logged = []
    window._append_log = logged.append
    dialog = game_ui.StoreGameDialog(_paths(tmp_path), lambda: None, window)
    dialog._builds = [
        Build("1", "Game", "b2", "2.0", 2),
        Build("1", "Game", "b1", "1.0", 1),
    ]
    dialog._job = SimpleNamespace(
        failed_versions=[("2.0 (2)", "not_launchable"), ("1.0 (1)", "failed")],
        failed_version_reasons={"1.0 (1)": "RuntimeError: HTTP 404"},
        error_detail="",
    )

    dialog._finish_install(None, "download_failed")

    assert len(notices) == 1
    # The library window has no way to open its Activity log, so the notice
    # itself says why each version failed.
    assert notices[0].splitlines()[2:] == [
        f"2.0 (2): {game_ui.ADD_GAME('version_not_launchable')}",
        f"1.0 (1): {game_ui.ADD_GAME('version_failed')} (RuntimeError: HTTP 404)",
    ]
    assert logged == ["\n1.0 (1): RuntimeError: HTTP 404\n"]
    dialog._job = None
    dialog._finish_install(None, "not_launchable")
    assert dialog.validation.text() == game_ui.ADD_GAME("not_launchable")
    dialog.close()


def test_versions_that_failed_for_the_same_reason_share_one_line(
    app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from riftlift import game_ui

    notices = []
    monkeypatch.setattr(
        game_ui,
        "_themed_notice",
        lambda _parent, _title, text, details="": notices.append(
            f"{text}\n\n{details}"
        ),
    )
    dialog = game_ui.StoreGameDialog(_paths(tmp_path), lambda: None)
    dialog._builds = [
        Build("1", "Game", f"b{code}", f"1.{code}", code) for code in (3, 2, 1)
    ]
    offline = "ConnectionError: network is unreachable"
    dialog._job = SimpleNamespace(
        failed_versions=[
            ("1.3 (3)", "failed"),
            ("1.2 (2)", "failed"),
            ("1.1 (1)", "failed"),
        ],
        failed_version_reasons={
            "1.3 (3)": offline,
            "1.2 (2)": offline,
            "1.1 (1)": offline,
        },
        error_detail="",
    )

    dialog._finish_install(None, "download_failed")

    assert notices[0].splitlines()[2:] == [
        f"1.3 (3), 1.2 (2), 1.1 (1): {game_ui.ADD_GAME('version_failed')} ({offline})"
    ]
    dialog.close()


@pytest.mark.parametrize(
    ("start", "thread_name"),
    [
        ("_load_versions", "riftlift-list-versions"),
        ("_check_catalog", "riftlift-link-validation"),
    ],
)
def test_a_link_check_finishing_after_the_dialog_is_gone_is_dropped(
    app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start: str, thread_name: str
) -> None:
    import threading

    import shiboken6

    from riftlift import game_ui

    started, release = threading.Event(), threading.Event()

    def slow(*_args):
        started.set()
        release.wait(5)
        return game_ui.LinkCheck("Game", [Build("1", "Game", "b", "1.0", 1)])

    monkeypatch.setattr(game_ui, "available_builds", lambda *_a: slow().builds)
    monkeypatch.setattr(game_ui, "check_store_link", slow)
    monkeypatch.setattr(game_ui, "_is_signed_in", lambda _paths: True)
    crashes = []
    monkeypatch.setattr(threading, "excepthook", crashes.append)
    url = "https://www.meta.com/experiences/pcvr/game/1234567890/"
    dialog = game_ui.StoreGameDialog(_paths(tmp_path), lambda: None)
    dialog.entry.setText(url)
    dialog.timer.stop()

    if start == "_load_versions":
        dialog._load_versions(url)
    else:
        dialog._check_catalog()
    assert started.wait(5)
    worker = next(t for t in threading.enumerate() if t.name == thread_name)
    # Quitting deletes the dialog's event objects while the worker still runs.
    shiboken6.delete(dialog.events)
    release.set()
    worker.join(5)

    assert not worker.is_alive()
    assert crashes == []
    dialog.close()


def test_resuming_a_paused_all_versions_run_does_not_ask_again(
    app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from riftlift import game_ui
    from riftlift.download_job import DownloadJob
    from riftlift.library import ALL_BUILDS

    questions, starts = [], []
    monkeypatch.setattr(
        game_ui,
        "_themed_question",
        lambda _parent, _title, text: questions.append(text) or True,
    )

    class PausingJob(DownloadJob):
        def start(self):
            starts.append(self.request["build"])
            self.paused.emit()

    monkeypatch.setattr(game_ui, "DownloadJob", PausingJob)
    url = "https://www.meta.com/experiences/pcvr/game/1/"
    dialog = game_ui.StoreGameDialog(_paths(tmp_path), lambda: None)
    dialog.entry.setText(url)
    dialog._verified_url = url
    dialog._game_name = "Game"
    dialog._set_builds(
        [Build("1", "Game", f"b{code}", f"1.{code}", code) for code in (2, 1)]
    )
    dialog.versions.setCurrentIndex(dialog.versions.findData(ALL_BUILDS))
    dialog.submit.setEnabled(True)  # as after the link check

    dialog.submit.click()
    assert dialog.submit.text() == game_ui.ADD_GAME("resume_download")
    dialog.submit.click()

    # Resuming continues the run the question already allowed.
    assert starts == [ALL_BUILDS, ALL_BUILDS]
    assert len(questions) == 1
    dialog.close()
