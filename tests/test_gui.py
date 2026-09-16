import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from riftlift.auth_browser import Browser
from riftlift.auth_ui import AuthDialog
from riftlift.cli import parser
from riftlift.config import Game, Paths, language_preference
from riftlift.entitlements import OwnedApp
from riftlift.game_ui import StoreGameDialog, is_valid_rift_store_url
from riftlift.i18n import current_language
from riftlift.main_window import Window
from riftlift.metadata import CatalogMetadata
from riftlift.playtime import add_playtime, mark_launch
from riftlift.util import RiftLiftError


def catalog_game(name: str = "Vader Immortal") -> CatalogMetadata:
    return CatalogMetadata(name, "", "", "", "", [], "")


def wait_until(app: QtWidgets.QApplication, condition, timeout: float = 2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_validates_meta_rift_store_urls() -> None:
    assert is_valid_rift_store_url(
        "https://www.meta.com/experiences/pcvr/vader-immortal/123456789/"
    )
    assert is_valid_rift_store_url(
        "https://meta.com/experiences/pcvr/lone-echo/123456789/?ref=library"
    )
    assert is_valid_rift_store_url(
        "https://www.meta.com/en-gb/experiences/pcvr/lone-echo/1368187813209608/"
    )
    assert not is_valid_rift_store_url("123456789")
    assert not is_valid_rift_store_url(
        "https://www.meta.com/experiences/quest/vader-immortal/123456789/"
    )
    assert not is_valid_rift_store_url(
        "https://example.com/experiences/pcvr/vader-immortal/123456789/"
    )
    assert not is_valid_rift_store_url(
        "https://www.meta.com/not-a-locale/experiences/pcvr/vader-immortal/123456789/"
    )


def test_gui_command_is_available() -> None:
    assert parser().parse_args(["gui"]).command == "gui"


def test_gui_exposes_only_the_primary_library_actions(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    buttons = {button.text() for button in window.findChildren(QtWidgets.QPushButton)}
    assert {
        "Generate a diagnostic report",
        "Sign In",
        "Steam Games",
        "Add Game",
        "⟳",
        "View Activity",
    } <= buttons
    assert "Refresh Info" not in buttons
    assert "Store" not in buttons
    assert "Open in Rift Store ↗" in buttons
    assert not window.findChildren(QtWidgets.QSpinBox)
    assert "Your Meta Rift library, lifted into Linux OpenXR" not in {
        label.text() for label in window.findChildren(QtWidgets.QLabel)
    }

    window.close()
    app.processEvents()


def test_game_detail_scrolls_instead_of_overlapping_when_space_is_tight(
    tmp_path: Path,
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    window.resize(1024, 637)
    window.show()
    window.setup_banner.show()  # takes up extra vertical space, like a real warning does
    (tmp_path / "game.exe").touch()
    game = Game(
        "g",
        "Epic Roller Coasters",
        "1",
        "k",
        str(tmp_path),
        "game.exe",
        [],
        description="A" * 2000,
    )
    window.installed = [game]
    window.show_game(game)
    app.processEvents()

    scroll = window.stack.parentWidget().parentWidget()
    assert isinstance(scroll, QtWidgets.QScrollArea)
    assert scroll.widgetResizable()
    # Force a viewport shorter than the content needs, regardless of how
    # much slack today's window/spacing happens to leave - what matters is
    # that a scrollbar engages rather than content overlapping.
    scroll.setFixedHeight(100)
    app.processEvents()
    assert window.stack.sizeHint().height() > scroll.viewport().height()
    vertical_scrollbar = scroll.verticalScrollBar()
    assert vertical_scrollbar.maximum() > vertical_scrollbar.minimum()

    window.close()
    app.processEvents()


def test_gui_cannot_close_while_an_operation_is_running(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    window.busy = True
    window.busy_label = "Launching Lone Echo"

    class CloseEvent:
        ignored = False

        def ignore(self):
            self.ignored = True

    event = CloseEvent()
    window.closeEvent(event)

    assert event.ignored
    assert "minimize RiftLift instead" in window.status.text()
    window.busy = False
    window.close()
    app.processEvents()


def test_window_auto_runs_setup_when_the_compatibility_runtime_is_not_ready(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    monkeypatch.setattr("riftlift.main_window.needs_setup", lambda _paths: True)
    monkeypatch.setattr(
        "riftlift.main_window.runtime_access_token", lambda _paths: "tok"
    )
    monkeypatch.setattr("riftlift.main_window.list_owned_pcvr_apps", lambda _token: [])
    calls = []
    monkeypatch.setattr("riftlift.main_window.setup", lambda _paths: calls.append(1))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    window = Window(paths)

    assert wait_until(app, lambda: not window.busy)
    assert calls == [1]
    assert window.status.text() == "Compatibility runtime is ready"
    window.close()
    app.processEvents()


def test_window_does_not_run_setup_when_the_compatibility_runtime_is_ready(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    monkeypatch.setattr("riftlift.main_window.needs_setup", lambda _paths: False)
    calls = []
    monkeypatch.setattr("riftlift.main_window.setup", lambda _paths: calls.append(1))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    window = Window(paths)
    app.processEvents()

    assert calls == []
    assert not window.busy
    window.close()
    app.processEvents()


def test_steam_store_fallback_never_opens_meta(tmp_path: Path, monkeypatch) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    (tmp_path / "Aircar.exe").touch()
    game = Game(
        "aircar",
        "Aircar",
        "1073390",
        "steam.app.1073390",
        str(tmp_path),
        "Aircar.exe",
        [],
        steam_app_id=1073390,
        source="steam",
    )
    game.save(paths)
    opened = []
    monkeypatch.setattr(
        "riftlift.main_window.QtGui.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()),
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    window.open_store()

    assert opened == ["https://store.steampowered.com/app/1073390/"]
    window.close()
    app.processEvents()


def test_library_refresh_continues_after_one_catalog_failure(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    for slug in ("first", "second"):
        Game(slug, slug.title(), slug, slug, str(tmp_path), f"{slug}.exe", []).save(
            paths
        )
    refreshed = []

    def populate(_paths, game, *, refresh=False):
        refreshed.append((game.slug, refresh))
        if game.slug == "first":
            raise RiftLiftError("catalog unavailable")

    monkeypatch.setattr("riftlift.main_window.populate_game_metadata", populate)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    window.refresh_library()
    assert wait_until(app, lambda: not window.busy)

    assert refreshed == [("first", True), ("second", True)]
    assert window.status.text() == "Library refreshed"
    window.close()
    app.processEvents()


def test_settings_debug_logging_toggle_persists_setting(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    window.show_settings()
    assert window.view_stack.currentIndex() == 1
    assert not window.settings_page.debug_logging.isChecked()
    window.settings_page.debug_logging.setChecked(True)
    assert (paths.config / "debug-logging").is_file()
    window._leave_settings()
    assert window.view_stack.currentIndex() == 0
    window.close()
    app.processEvents()

    restored = Window(paths)
    restored.show_settings()
    assert restored.settings_page.debug_logging.isChecked()
    restored.settings_page.debug_logging.setChecked(False)
    assert not (paths.config / "debug-logging").exists()
    restored.close()
    app.processEvents()


def test_header_button_toggles_between_settings_and_library(
    tmp_path: Path,
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    assert window.settings_button.text() == "Settings"
    window.settings_button.click()
    assert window.view_stack.currentIndex() == 1
    assert window.settings_button.text() == "Library"
    window.settings_button.click()
    assert window.view_stack.currentIndex() == 0
    assert window.settings_button.text() == "Settings"

    window.close()
    app.processEvents()


def test_settings_page_is_wrapped_in_a_scroll_area(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    scroll = window.settings_page.parentWidget().parentWidget()
    assert isinstance(scroll, QtWidgets.QScrollArea)
    assert scroll.widgetResizable()
    assert scroll is window.view_stack.widget(1)

    window.close()
    app.processEvents()


def test_settings_page_runs_the_system_check(tmp_path: Path, monkeypatch) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    calls = []
    monkeypatch.setattr("riftlift.main_window.doctor", lambda _paths: calls.append(1))
    monkeypatch.setattr(
        "riftlift.main_window.runtime_access_token", lambda _paths: "tok"
    )
    monkeypatch.setattr("riftlift.main_window.list_owned_pcvr_apps", lambda _token: [])
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    window.show_settings()
    run_check = next(
        button
        for button in window.settings_page.findChildren(QtWidgets.QPushButton)
        if button.text() == "Generate a diagnostic report"
    )
    run_check.click()

    assert wait_until(app, lambda: not window.busy)
    assert calls == [1]
    assert window.status.text() == "Done"


def test_setup_banner_shows_only_when_the_runtime_needs_setup(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    monkeypatch.setattr(
        "riftlift.main_window.runtime_access_token", lambda _paths: "tok"
    )
    monkeypatch.setattr("riftlift.main_window.list_owned_pcvr_apps", lambda _token: [])
    monkeypatch.setattr("riftlift.main_window.needs_setup", lambda _paths: True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    assert wait_until(app, lambda: not window.setup_banner.isHidden())

    window.close()
    app.processEvents()

    monkeypatch.setattr("riftlift.main_window.needs_setup", lambda _paths: False)
    window = Window(paths)

    assert wait_until(app, lambda: window.setup_banner.isHidden())

    window.close()
    app.processEvents()


def test_setup_banner_button_runs_setup_and_hides_once_done(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    monkeypatch.setattr(
        "riftlift.main_window.runtime_access_token", lambda _paths: "tok"
    )
    monkeypatch.setattr("riftlift.main_window.list_owned_pcvr_apps", lambda _token: [])
    monkeypatch.setattr("riftlift.main_window._themed_error", lambda *a: None)
    needed = [True]
    monkeypatch.setattr("riftlift.main_window.needs_setup", lambda _paths: needed[0])
    attempts = []

    def failing_setup(_paths):
        # The auto-run at startup attempts setup silently and fails here, the
        # way it would for a user whose setup genuinely doesn't succeed on
        # its own - that's the case the banner exists to surface.
        attempts.append("auto")
        raise RiftLiftError("boom")

    monkeypatch.setattr("riftlift.main_window.setup", failing_setup)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    assert wait_until(app, lambda: not window.busy)
    assert wait_until(app, lambda: not window.setup_banner.isHidden())

    def succeeding_setup(_paths):
        attempts.append("manual")
        needed[0] = False

    monkeypatch.setattr("riftlift.main_window.setup", succeeding_setup)
    run_now = next(
        button
        for button in window.setup_banner.findChildren(QtWidgets.QPushButton)
        if button.text() == "Set up now"
    )
    run_now.click()

    assert wait_until(app, lambda: not window.busy)
    assert attempts == ["auto", "manual"]
    assert wait_until(app, lambda: window.setup_banner.isHidden())

    window.close()
    app.processEvents()


def test_settings_page_runs_setup(tmp_path: Path, monkeypatch) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    calls = []
    monkeypatch.setattr("riftlift.main_window.setup", lambda _paths: calls.append(1))
    monkeypatch.setattr(
        "riftlift.main_window.runtime_access_token", lambda _paths: "tok"
    )
    monkeypatch.setattr("riftlift.main_window.list_owned_pcvr_apps", lambda _token: [])
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    window.show_settings()
    run_setup = next(
        button
        for button in window.settings_page.findChildren(QtWidgets.QPushButton)
        if button.text() == "Run setup"
    )
    run_setup.click()

    assert wait_until(app, lambda: not window.busy)
    assert calls == [1]


def test_settings_status_reports_needs_setup_before_the_openxr_check(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    monkeypatch.setattr("riftlift.main_window.needs_setup", lambda _paths: True)
    monkeypatch.setattr(
        "riftlift.main_window.active_runtime_json",
        lambda: (_ for _ in ()).throw(AssertionError("should short-circuit")),
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    window.show_settings()
    assert wait_until(
        app, lambda: window.settings_page.status_text.text() != "Checking..."
    )
    assert window.settings_page.status_icon.text() == "✗"
    assert (
        "compatibility runtime isn't set up" in window.settings_page.status_text.text()
    )

    window.close()
    app.processEvents()


def test_settings_status_reports_missing_openxr_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    monkeypatch.setattr("riftlift.main_window.needs_setup", lambda _paths: False)
    monkeypatch.setattr(
        "riftlift.main_window.active_runtime_json",
        lambda: (_ for _ in ()).throw(RiftLiftError("no active OpenXR runtime")),
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    window.show_settings()
    assert wait_until(
        app, lambda: window.settings_page.status_text.text() != "Checking..."
    )
    assert window.settings_page.status_icon.text() == "✗"
    assert "Monado or WiVRn" in window.settings_page.status_text.text()

    window.close()
    app.processEvents()


def test_settings_status_reports_healthy_when_everything_matches(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    monkeypatch.setattr("riftlift.main_window.needs_setup", lambda _paths: False)
    monkeypatch.setattr(
        "riftlift.main_window.active_runtime_json", lambda: tmp_path / "manifest.json"
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    window.show_settings()
    assert wait_until(
        app, lambda: window.settings_page.status_text.text() != "Checking..."
    )
    assert window.settings_page.status_icon.text() == "✓"
    assert window.settings_page.status_text.text() == "Everything looks ready."

    window.close()
    app.processEvents()

    window.close()
    app.processEvents()

    window.close()
    app.processEvents()


def test_confirmed_language_change_persists_and_restarts_the_window(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    window.show_settings()

    monkeypatch.setattr("riftlift.main_window._themed_question", lambda *a: True)
    window.settings_page.language.setCurrentIndex(
        window.settings_page._codes.index("fr")
    )

    assert language_preference(paths) == "fr"
    assert current_language() == "fr"
    assert app._riftlift_window is not window
    new_window = app._riftlift_window
    assert new_window.isVisible()
    new_window.close()
    app.processEvents()


def test_language_change_preserves_the_selected_installed_game(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    (tmp_path / "a.exe").touch()
    (tmp_path / "b.exe").touch()
    Game("game-a", "Game A", "1", "a.game", str(tmp_path), "a.exe", []).save(paths)
    Game("game-b", "Game B", "2", "b.game", str(tmp_path), "b.exe", []).save(paths)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    window.refresh()
    for game in window.installed:
        if game.slug == "game-b":
            window.show_game(game)

    monkeypatch.setattr("riftlift.main_window._themed_question", lambda *a: True)
    window.show_settings()
    window.settings_page.language.setCurrentIndex(
        window.settings_page._codes.index("fr")
    )

    new_window = app._riftlift_window
    assert new_window is not window
    assert new_window.slug == "game-b"
    assert new_window.game_name.text() == "Game B"
    new_window.close()
    app.processEvents()


def test_language_change_preserves_the_selected_owned_game(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    owned = [
        OwnedApp(app_id="111", name="Alpha", slug="alpha"),
        OwnedApp(app_id="222", name="Beta", slug="beta"),
    ]
    monkeypatch.setattr(
        "riftlift.main_window.runtime_access_token", lambda _paths: "tok"
    )
    monkeypatch.setattr(
        "riftlift.main_window.list_owned_pcvr_apps", lambda _token: owned
    )
    monkeypatch.setattr(
        "riftlift.main_window.fetch_owned_icon", lambda _paths, _id: None
    )
    monkeypatch.setattr(
        "riftlift.main_window.fetch_owned_metadata",
        lambda _paths, _id, refresh=False: None,
    )
    monkeypatch.setattr(
        "riftlift.main_window.fetch_owned_hero",
        lambda _paths, _id, refresh=False: None,
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    assert wait_until(app, lambda: len(window.owned) == 2)
    window.show_owned(owned[1])

    monkeypatch.setattr("riftlift.main_window._themed_question", lambda *a: True)
    window.show_settings()
    window.settings_page.language.setCurrentIndex(
        window.settings_page._codes.index("fr")
    )

    new_window = app._riftlift_window
    assert new_window is not window
    assert wait_until(app, lambda: new_window.selected_owned == owned[1])
    assert new_window.game_name.text() == "Beta"
    new_window.close()
    app.processEvents()


def test_language_change_invalidates_the_owned_metadata_cache(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    cache_dir = paths.cache / "owned-metadata"
    cache_dir.mkdir(parents=True)
    (cache_dir / "111.en.json").write_text("{}")

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    window.show_settings()

    monkeypatch.setattr("riftlift.main_window._themed_question", lambda *a: True)
    window.settings_page.language.setCurrentIndex(
        window.settings_page._codes.index("fr")
    )

    assert not cache_dir.exists()
    new_window = app._riftlift_window
    new_window.close()
    app.processEvents()


def test_declined_language_change_reverts_the_dropdown(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    window.show_settings()

    monkeypatch.setattr("riftlift.main_window._themed_question", lambda *a: False)
    window.settings_page.language.setCurrentIndex(
        window.settings_page._codes.index("fr")
    )

    assert language_preference(paths) == "auto"
    assert window.settings_page.language.currentText() == "Auto"
    window.close()
    app.processEvents()


def test_language_change_is_refused_while_an_operation_is_running(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    window.show_settings()
    window.busy = True

    asked = []
    shown_errors = []
    monkeypatch.setattr(
        "riftlift.main_window._themed_question", lambda *a: asked.append(a) or True
    )
    monkeypatch.setattr(
        "riftlift.main_window._themed_error", lambda *a: shown_errors.append(a)
    )
    window.settings_page.language.setCurrentIndex(
        window.settings_page._codes.index("fr")
    )

    assert asked == []
    assert len(shown_errors) == 1
    assert language_preference(paths) == "auto"
    window.busy = False
    window.close()
    app.processEvents()


def test_store_action_matches_the_selected_game_source(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    rift = Game("rift", "Rift Game", "123", "rift.game", "/tmp", "game.exe", [])
    steam = Game(
        "steam",
        "Steam Game",
        "456",
        "steam.app.456",
        "/tmp",
        "game.exe",
        [],
        store_url="https://store.steampowered.com/app/456/",
        source="steam",
    )

    window.show_game(steam)
    assert window.store_link.text() == "Open in Steam ↗"
    assert window.meta.text().startswith("Steam app 456")
    window.show_game(rift)
    assert window.store_link.text() == "Open in Rift Store ↗"
    local = Game(
        "local",
        "Local Game",
        "",
        "local.local-game",
        "/tmp",
        "game.exe",
        [],
        source="local",
    )
    window.show_game(local)
    assert not window.store_link.isVisible()

    window.close()
    app.processEvents()


def test_meta_row_gives_the_stretch_to_whichever_label_holds_the_text(
    tmp_path: Path,
) -> None:
    # self.meta and self.meta_detail must never both stretch at once: an
    # installed game's long details line (self.meta) needs the full row
    # width, but an owned app's short "Not installed" (also self.meta)
    # must not stretch and leave a big gap before meta_detail.
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    game = Game("g", "Installed Game", "1", "k", "/tmp", "game.exe", [])

    window.show_game(game)
    assert window.meta_row.stretch(0) == 1
    assert window.meta_row.stretch(1) == 0

    window.show_owned(OwnedApp(app_id="2", name="Owned Game", slug="owned-game"))
    assert window.meta_row.stretch(0) == 0
    assert window.meta_row.stretch(1) == 1

    window.close()
    app.processEvents()


def test_game_description_refreshes_only_when_its_language_is_stale(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    fetched = []

    def fetch(app_id):
        fetched.append(app_id)
        return CatalogMetadata(
            "Rift Game", "", "Refreshed description", "Dev", "Pub", ["Action"], ""
        )

    monkeypatch.setattr("riftlift.main_window.fetch_catalog_metadata", fetch)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    current_language_game = Game(
        "current",
        "Current Language Game",
        "111",
        "current.game",
        "/tmp",
        "game.exe",
        [],
        description="Already in English",
        description_lang="en",
    )
    window.installed = [current_language_game]
    window.show_game(current_language_game)
    app.processEvents()
    assert fetched == []
    assert window.description_label.text() == "Already in English"

    stale_game = Game(
        "stale",
        "Stale Language Game",
        "222",
        "stale.game",
        "/tmp",
        "game.exe",
        [],
        description="Ancienne description en francais",
        description_lang="fr",
    )
    window.installed = [current_language_game, stale_game]
    window.show_game(stale_game)
    assert wait_until(app, lambda: fetched == ["222"])
    assert wait_until(
        app, lambda: window.description_label.text() == "Refreshed description"
    )
    assert stale_game.description_lang == "en"
    assert Game.load(paths, "stale").description == "Refreshed description"

    window.close()
    app.processEvents()


def test_a_game_with_unknown_description_language_always_refreshes(
    tmp_path: Path, monkeypatch
) -> None:
    # A game installed before description_lang existed (or whose content
    # came from IP-geolocated defaults rather than an explicit language
    # request) must never be assumed to already match the current UI
    # language - only an explicit, already-tagged match should skip a
    # refresh.
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    fetched = []

    def fetch(app_id):
        fetched.append(app_id)
        return CatalogMetadata(
            "Rift Game", "", "Refreshed description", "Dev", "Pub", ["Action"], ""
        )

    monkeypatch.setattr("riftlift.main_window.fetch_catalog_metadata", fetch)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    legacy_game = Game(
        "legacy",
        "Legacy Game",
        "333",
        "legacy.game",
        "/tmp",
        "game.exe",
        [],
        description="Some old cached text",
        description_lang="",
    )
    window.installed = [legacy_game]
    window.show_game(legacy_game)

    assert wait_until(app, lambda: fetched == ["333"])
    assert wait_until(
        app, lambda: window.description_label.text() == "Refreshed description"
    )
    assert legacy_game.description_lang == "en"

    window.close()
    app.processEvents()


def test_description_refresh_ignores_a_meta_games_steam_shortcut_id(
    tmp_path: Path, monkeypatch
) -> None:
    # A Meta game that was also synced to Steam ("Add to Steam") ends up
    # with a non-zero steam_app_id too, but that's Steam's own generated
    # shortcut id, not a real Meta catalog id - refreshing its description
    # must still fetch by app_id, not by that unrelated shortcut id.
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    fetched = []

    def fetch(app_id):
        fetched.append(app_id)
        return CatalogMetadata(
            "Rift Game", "", "Refreshed description", "Dev", "Pub", ["Action"], ""
        )

    monkeypatch.setattr("riftlift.main_window.fetch_catalog_metadata", fetch)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    synced_game = Game(
        "synced",
        "Synced Meta Game",
        "1711938725528735",
        "meta.synced",
        "/tmp",
        "game.exe",
        [],
        source="meta",
        steam_app_id=3820161062,
        description="Ancienne description en francais",
        description_lang="fr",
    )
    window.installed = [synced_game]
    window.show_game(synced_game)

    assert wait_until(app, lambda: fetched == ["1711938725528735"])

    window.close()
    app.processEvents()


def test_selected_game_shows_local_playtime(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    mark_launch(paths, "echo")
    add_playtime(paths, "echo", 7380)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    game = Game("echo", "Echo", "", "local.echo", "/tmp", "echo.exe", [])

    window.show_game(game)

    assert "2h 3m played" in window.meta.text()
    window.close()
    app.processEvents()


def test_local_game_dialog_requires_an_existing_executable(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    dialogs = []
    monkeypatch.setattr(
        QtWidgets.QDialog, "exec", lambda dialog: dialogs.append(dialog)
    )

    window.local_dialog()
    dialog = dialogs[0]
    executable = next(
        entry
        for entry in dialog.findChildren(QtWidgets.QLineEdit)
        if entry.placeholderText() == "/path/to/game.exe"
    )
    add_button = next(
        button
        for button in dialog.findChildren(QtWidgets.QPushButton)
        if button.text() == "Add"
    )
    assert not add_button.isEnabled()
    game = tmp_path / "Local Game.exe"
    game.write_bytes(b"MZ")
    executable.setText(str(game))
    assert add_button.isEnabled()

    dialog.close()
    window.close()
    app.processEvents()


def test_auth_dialog_uses_one_default_browser_action(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(
        "riftlift.auth_ui.default_browser",
        lambda: Browser("firefox", "Firefox", "firefox", ("firefox",)),
    )
    monkeypatch.setattr(
        "riftlift.auth_ui.QtCore.QTimer.singleShot", lambda *_args: None
    )

    dialog = AuthDialog(paths)
    buttons = {button.text() for button in dialog.findChildren(QtWidgets.QPushButton)}

    assert "Open default browser" in buttons
    assert not any(text.startswith("Continue with") for text in buttons)
    assert "Meta Horizon Link" not in " ".join(buttons)
    assert "default browser" in dialog.status.text()
    dialog.close()
    app.processEvents()


def test_auth_dialog_detects_browser_completion_and_returns(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    stopped = []

    class Process:
        def poll(self):
            return 0

        def terminate(self):
            stopped.append(True)

    monkeypatch.setattr(
        "riftlift.auth_ui.default_browser",
        lambda: Browser("edge", "Microsoft Edge", "chromium", ("edge",)),
    )
    monkeypatch.setattr(
        "riftlift.auth_ui.QtCore.QTimer.singleShot", lambda *_args: None
    )
    monkeypatch.setattr(
        "riftlift.auth_ui.launch_browser_login", lambda *_args: Process()
    )
    token = "FRL" + "a" * 176
    callbacks = iter([False, True])
    session = SimpleNamespace(
        login_url="https://auth.meta.com/native_sso/confirm",
        callback_ready=lambda: next(callbacks),
        complete=lambda: token,
    )
    monkeypatch.setattr(
        "riftlift.auth_ui.MetaAuthSession.begin", lambda _paths: session
    )

    dialog = AuthDialog(paths)

    dialog.start()
    assert wait_until(app, lambda: dialog.pending is not None and dialog.pending.done())
    dialog.check_login()
    dialog.check_login()
    assert dialog.operation == "waiting"
    assert dialog.timer.isActive()
    dialog.check_login()
    assert wait_until(app, lambda: dialog.pending is not None and dialog.pending.done())
    dialog.check_login()

    assert dialog.completed
    assert (paths.config / "meta-access-token").read_text().strip() == token
    assert not stopped
    assert dialog.status.text() == "Signed in. Returning to RiftLift…"
    dialog.close()
    app.processEvents()


def test_auth_dialog_preserves_browser_after_login_error(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    stopped = []
    process = SimpleNamespace(poll=lambda: None, terminate=lambda: stopped.append(True))
    monkeypatch.setattr(
        "riftlift.auth_ui.QtCore.QTimer.singleShot", lambda *_args: None
    )
    dialog = AuthDialog(paths)
    dialog.browser = Browser("edge", "Microsoft Edge", "chromium", ("edge",))
    dialog.process = process

    dialog.show_error("Meta rejected the token")

    assert not stopped
    assert dialog.process is None
    assert dialog.status.text() == "Meta rejected the token"
    dialog.close()
    app.processEvents()


def test_signed_in_window_uses_account_label(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    (paths.config / "meta-access-token").write_text("FRL" + "a" * 176)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    window = Window(paths)

    assert window.signin.text() == "Account"
    window.close()
    app.processEvents()


def test_install_stays_disabled_until_rift_link_is_valid(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    dialogs = []
    monkeypatch.setattr("riftlift.game_ui.LINK_VALIDATION_DELAY_MS", 0)
    monkeypatch.setattr(
        "riftlift.game_ui.fetch_catalog_metadata", lambda _app_id: catalog_game()
    )
    monkeypatch.setattr(
        QtWidgets.QDialog, "exec", lambda dialog: dialogs.append(dialog)
    )

    window.add_dialog()
    dialog = dialogs[0]
    assert any(
        button.text() == "Add a local game…"
        for button in dialog.findChildren(QtWidgets.QPushButton)
    )
    entry = dialog.findChild(QtWidgets.QLineEdit)
    install = next(
        button
        for button in dialog.findChildren(QtWidgets.QPushButton)
        if button.text() == "Install"
    )
    cancel = next(
        button
        for button in dialog.findChildren(QtWidgets.QPushButton)
        if button.text() == "Cancel"
    )
    assert cancel.icon().isNull()
    assert not install.isEnabled()
    entry.setText("https://www.meta.com/experiences/pcvr/vader-immortal/123456789/")
    assert not install.isEnabled()
    assert wait_until(app, install.isEnabled)
    entry.setText("https://example.com/not-a-rift-link")
    assert not install.isEnabled()

    dialog.close()
    window.close()
    app.processEvents()


def test_add_dialog_prefill_installs_and_reports_progress(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr("riftlift.game_ui.LINK_VALIDATION_DELAY_MS", 0)
    monkeypatch.setattr(
        "riftlift.game_ui.fetch_catalog_metadata",
        lambda _app_id: catalog_game("Lone Echo"),
    )
    fake_game = Game(
        "lone-echo",
        "Lone Echo",
        "123456789",
        "meta.lone-echo",
        str(tmp_path / "games/lone-echo"),
        "LoneEcho.exe",
        [],
    )
    progress_lines = []

    def fake_add(_paths, url):
        assert url == "https://www.meta.com/experiences/pcvr/lone-echo/123456789/"
        print("Preparing 2 unique segments with 8 workers...")
        print("  segments 1/2 (0 cached)")
        print("  segments 2/2 (0 cached)")
        return fake_game

    monkeypatch.setattr("riftlift.game_ui.add", fake_add)
    monkeypatch.setattr("riftlift.game_ui.sync_with_restart", lambda _paths: "ok")

    dialog = StoreGameDialog(
        paths,
        lambda: None,
        initial_url="https://www.meta.com/experiences/pcvr/lone-echo/123456789/",
    )
    dialog.install_events.progress.connect(
        lambda label, current, total: progress_lines.append((label, current, total))
    )
    assert wait_until(app, lambda: dialog.submit.isEnabled())

    dialog.submit.click()

    assert wait_until(app, lambda: dialog.installed_game is not None)
    assert dialog.installed_game.slug == "lone-echo"
    assert ("Downloading", 2, 2) in progress_lines
    dialog.close()
    app.processEvents()


def test_owned_library_merges_with_installed_without_duplicates(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    game_dir = tmp_path / "games/echo-vr"
    game_dir.mkdir(parents=True)
    (game_dir / "echo.exe").touch()
    Game("echo-vr", "Echo VR", "111", "meta.echo", str(game_dir), "echo.exe", []).save(
        paths
    )
    owned = [
        OwnedApp(app_id="111", name="Echo VR", slug="echo-vr"),
        OwnedApp(app_id="222", name="Lone Echo", slug="lone-echo"),
    ]
    monkeypatch.setattr(
        "riftlift.main_window.runtime_access_token", lambda _paths: "tok"
    )
    monkeypatch.setattr(
        "riftlift.main_window.list_owned_pcvr_apps", lambda _token: owned
    )
    monkeypatch.setattr(
        "riftlift.main_window.fetch_owned_icon", lambda _paths, _id: None
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    assert wait_until(app, lambda: window.owned)

    assert window._installed_category.childCount() == 1
    assert window._installed_category.child(0).text(0) == "Echo VR"
    assert window._owned_category.childCount() == 1
    assert window._owned_category.child(0).text(0) == "Lone Echo"

    window.close()
    app.processEvents()


def test_installed_steam_games_get_their_own_category(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    (tmp_path / "meta.exe").touch()
    (tmp_path / "steam.exe").touch()
    Game(
        "meta-game", "Meta Game", "111", "meta.game", str(tmp_path), "meta.exe", []
    ).save(paths)
    Game(
        "steam-game",
        "Steam Game",
        "222",
        "steam.app.222",
        str(tmp_path),
        "steam.exe",
        [],
        steam_app_id=222,
        source="steam",
    ).save(paths)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    assert window._installed_category.childCount() == 1
    assert window._installed_category.child(0).text(0) == "Meta Game"
    assert window._steam_category.childCount() == 1
    assert window._steam_category.child(0).text(0) == "Steam Game"
    assert not window._steam_category.isHidden()

    window.close()
    app.processEvents()


def test_empty_categories_are_not_shown_at_all(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    (tmp_path / "meta.exe").touch()
    Game(
        "meta-game", "Meta Game", "111", "meta.game", str(tmp_path), "meta.exe", []
    ).save(paths)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    assert not window._installed_category.isHidden()
    assert window._steam_category.childCount() == 0
    assert window._steam_category.isHidden()
    assert window._owned_category.childCount() == 0
    assert window._owned_category.isHidden()

    window.close()
    app.processEvents()


def test_uninstall_button_wording_matches_the_game_source(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    meta_game = Game(
        "meta-game", "Meta Game", "111", "meta.game", "/tmp", "game.exe", []
    )
    steam_game = Game(
        "steam-game",
        "Steam Game",
        "222",
        "steam.app.222",
        "/tmp",
        "game.exe",
        [],
        steam_app_id=222,
        source="steam",
    )

    window.show_game(meta_game)
    assert window.uninstall_button.text() == "Uninstall"
    window.show_game(steam_game)
    assert window.uninstall_button.text() == "Remove from RiftLift"

    window.close()
    app.processEvents()


def test_removing_a_steam_game_does_not_restart_steam(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    (tmp_path / "game.exe").touch()
    steam_game = Game(
        "steam-game",
        "Steam Game",
        "222",
        "steam.app.222",
        str(tmp_path),
        "game.exe",
        [],
        steam_app_id=222,
        source="steam",
    )
    steam_game.save(paths)

    synced = []
    monkeypatch.setattr(
        "riftlift.main_window.sync_with_restart", lambda _paths: synced.append(True)
    )
    monkeypatch.setattr("riftlift.main_window._themed_question", lambda *a: True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    window.installed = [steam_game]
    window.show_game(steam_game)

    window.uninstall_selected()
    assert wait_until(app, lambda: not window.busy)

    assert synced == []
    window.close()
    app.processEvents()


def test_falls_back_to_first_owned_game_when_nothing_installed(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    owned = [OwnedApp(app_id="222", name="Lone Echo", slug="lone-echo")]
    monkeypatch.setattr(
        "riftlift.main_window.runtime_access_token", lambda _paths: "tok"
    )
    monkeypatch.setattr(
        "riftlift.main_window.list_owned_pcvr_apps", lambda _token: owned
    )
    monkeypatch.setattr(
        "riftlift.main_window.fetch_owned_icon", lambda _paths, _id: None
    )
    monkeypatch.setattr(
        "riftlift.main_window.fetch_owned_metadata",
        lambda _paths, _id, refresh=False: None,
    )
    monkeypatch.setattr(
        "riftlift.main_window.fetch_owned_hero",
        lambda _paths, _id, refresh=False: None,
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    assert wait_until(app, lambda: window.stack.currentIndex() == 1)
    assert window.game_name.text() == "Lone Echo"
    assert not window.install_here.isHidden()
    assert window.launch.isHidden()

    assert window.selected_owned == owned[0]
    opened = []
    add_dialog_calls = []
    monkeypatch.setattr(
        "riftlift.main_window.QtGui.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()),
    )
    monkeypatch.setattr(
        window,
        "add_dialog",
        lambda **kwargs: add_dialog_calls.append(kwargs),
    )

    window.install_here.click()
    assert add_dialog_calls == [
        {"initial_url": owned[0].store_url, "simple_name": owned[0].name}
    ]

    window.open_store()
    assert opened == [owned[0].store_url]

    window.close()
    app.processEvents()


def test_refresh_button_forces_owned_detail_refresh(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    window.selected_owned = OwnedApp(app_id="222", name="Lone Echo", slug="lone-echo")

    monkeypatch.setattr(window, "refresh_library", lambda: None)
    monkeypatch.setattr(window, "refresh_owned", lambda: None)
    metadata_calls = []
    portrait_calls = []
    monkeypatch.setattr(
        "riftlift.main_window.fetch_owned_metadata",
        lambda _paths, _id, refresh=False: metadata_calls.append(refresh) or None,
    )
    monkeypatch.setattr(
        "riftlift.main_window.fetch_owned_hero",
        lambda _paths, _id, refresh=False: portrait_calls.append(refresh) or None,
    )

    window.refresh_all()
    assert wait_until(app, lambda: metadata_calls == [True])
    assert portrait_calls == [True]

    window.close()
    app.processEvents()


def test_owned_game_meta_line_keeps_saying_not_installed(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    owned = [OwnedApp(app_id="222", name="Lone Echo", slug="lone-echo")]
    monkeypatch.setattr(
        "riftlift.main_window.runtime_access_token", lambda _paths: "tok"
    )
    monkeypatch.setattr(
        "riftlift.main_window.list_owned_pcvr_apps", lambda _token: owned
    )
    monkeypatch.setattr(
        "riftlift.main_window.fetch_owned_icon", lambda _paths, _id: None
    )
    monkeypatch.setattr(
        "riftlift.main_window.fetch_owned_metadata",
        lambda _paths, _id, refresh=False: CatalogMetadata(
            "Lone Echo", "", "", "Ready At Dawn", "", ["Action"], ""
        ),
    )
    monkeypatch.setattr(
        "riftlift.main_window.fetch_owned_hero",
        lambda _paths, _id, refresh=False: None,
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)

    assert wait_until(app, lambda: "Ready At Dawn" in window.meta_detail.text())
    assert window.meta.text() == "Not installed"
    assert not window.meta_skeleton.isVisible()

    window.close()
    app.processEvents()


def test_library_install_opens_a_simplified_confirmation_dialog(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    window.selected_owned = OwnedApp(app_id="222", name="Lone Echo", slug="lone-echo")
    dialogs = []
    monkeypatch.setattr(
        QtWidgets.QDialog, "exec", lambda dialog: dialogs.append(dialog)
    )

    window.install_owned()

    dialog = dialogs[0]
    assert dialog.entry.isHidden()
    assert dialog.submit.isEnabled()
    assert "Install Lone Echo?" in dialog.validation.text()
    local_button = next(
        button
        for button in dialog.findChildren(QtWidgets.QPushButton)
        if button.text() == "Add a local game…"
    )
    assert local_button.isHidden()

    dialog.close()
    window.close()
    app.processEvents()


def test_install_stays_disabled_when_rift_game_does_not_exist(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    paths.create()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(paths)
    dialogs = []
    monkeypatch.setattr("riftlift.game_ui.LINK_VALIDATION_DELAY_MS", 0)

    def missing(_app_id: str):
        raise RiftLiftError("Meta's store page has no catalog metadata for app 123")

    monkeypatch.setattr("riftlift.game_ui.fetch_catalog_metadata", missing)
    monkeypatch.setattr(
        QtWidgets.QDialog, "exec", lambda dialog: dialogs.append(dialog)
    )

    window.add_dialog()
    dialog = dialogs[0]
    entry = dialog.findChild(QtWidgets.QLineEdit)
    install = next(
        button
        for button in dialog.findChildren(QtWidgets.QPushButton)
        if button.text() == "Install"
    )
    entry.setText("https://www.meta.com/experiences/pcvr/not-real/123456789/")
    assert wait_until(
        app,
        lambda: any(
            label.text() == "This Rift store game could not be found."
            for label in dialog.findChildren(QtWidgets.QLabel)
        ),
    )
    assert not install.isEnabled()

    dialog.close()
    window.close()
    app.processEvents()


@pytest.mark.parametrize("action", ["reset_login", "accept", "reject"])
def test_auth_dialog_leaves_personal_browser_running(tmp_path, monkeypatch, action):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr("riftlift.auth_ui.QtCore.QTimer.singleShot", lambda *_: None)
    stopped = []
    dialog = AuthDialog(paths)
    dialog.browser = Browser("firefox", "Firefox", "firefox", ("firefox",))
    dialog.process = SimpleNamespace(
        poll=lambda: None, terminate=lambda: stopped.append(True)
    )
    getattr(dialog, action)()
    assert not stopped
    assert dialog.process is None
    dialog.close()
    app.processEvents()


def test_auth_dialog_reports_browser_launch_failure(tmp_path, monkeypatch):
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr("riftlift.auth_ui.QtCore.QTimer.singleShot", lambda *_: None)
    dialog = AuthDialog(paths)
    dialog.session = SimpleNamespace(callback_ready=lambda: False)
    dialog.process = SimpleNamespace(poll=lambda: 1)
    dialog.operation = "waiting"
    dialog.check_login()
    assert dialog.operation == "idle"
    assert "Could not open the browser" in dialog.status.text()
    dialog.close()
    app.processEvents()
