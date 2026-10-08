"""Offline tests can run on Windows with --confcutdir=tests/native_ui.

These test the actual Qt widget interactions, not credentials or runtime setup.
"""

import os

import pytest

if os.name != "nt":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtTest, QtWidgets

from riftlift.i18n import set_language
from riftlift.native_shell import brand_icon
from riftlift.ui_preview import PreviewWindow


@pytest.fixture(scope="session")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(app):
    set_language("en")
    widget = PreviewWindow()
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def test_actual_window_fixture_does_not_start_account_or_runtime(app, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external services are forbidden in UI fixtures")

    for name in ("is_signed_in", "runtime_access_token", "needs_setup", "setup"):
        monkeypatch.setattr(f"riftlift.main_window.{name}", forbidden)
    widget = PreviewWindow()
    widget.show()
    app.processEvents()
    assert not brand_icon().isNull()
    widget.close()
    widget.deleteLater()


def test_repeated_filter_reset_and_empty_recovery(window, app):
    for _ in range(5):
        window.search.setText("ASTER")
        assert not window._installed_category.child(0).isHidden()
        assert window._installed_category.child(1).isHidden()
        window.search.setText("no such title")
        assert window.search_empty.isVisible()
        window.search.clear()
        assert not window.search_empty.isVisible()
        assert not window._owned_category.child(1).isHidden()


def test_selection_switches_the_actual_primary_action(window, app):
    for _ in range(5):
        window.tree.setCurrentItem(window._owned_category.child(0))
        assert window.install_here.isVisible()
        assert not window.launch.isVisible()
        window.install_here.click()
        assert window.actions[-1] == "Download"
        window.tree.setCurrentItem(window._installed_category.child(0))
        assert window.launch.isVisible()
        assert not window.install_here.isVisible()
        window.launch.click()
        assert window.actions[-1] == "Play"


def test_keyboard_search_tab_selection_and_activation(window, app):
    window.activateWindow()
    window.search.setFocus()
    QtTest.QTest.keyClicks(window.search, "Signal")
    QtTest.QTest.keyClick(window.search, QtCore.Qt.Key_Tab)
    assert window.tree.hasFocus()
    window.tree.setCurrentItem(window._installed_category.child(1))
    assert window.game_name.text() == "Signal Reach"
    window.launch.setFocus()
    QtTest.QTest.keyClick(window.launch, QtCore.Qt.Key_Space)
    assert window.actions[-1] == "Play"


def test_settings_escape_returns_to_library(window, app):
    window.settings_button.click()
    assert window.view_stack.currentIndex() == 1
    QtTest.QTest.keyClick(window, QtCore.Qt.Key_Escape)
    assert window.view_stack.currentIndex() == 0
    assert window.tree.hasFocus()


def test_accessible_action_names_follow_backend_state_labels(window, app):
    window.launch.setText("Running")
    assert window.launch.accessibleName() == "Running"
    window.signin.setText("Account")
    assert window.signin.accessibleName() == "Account"


@pytest.mark.parametrize("state", ["signed-out", "owned", "loading", "error"])
def test_fixture_states_render_and_recover(app, state):
    widget = PreviewWindow(state)
    widget.show()
    app.processEvents()
    assert not widget.grab().isNull()
    if state == "signed-out":
        assert widget.stack.currentIndex() == 0
    if state == "loading":
        assert widget.description_skeleton.isVisible()
    widget.refresh_button.click()
    assert widget.actions[-1] == "Refresh"
    widget.close()
    widget.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


@pytest.mark.parametrize("language", ["en", "fr"])
def test_compact_view_has_visible_unclipped_primary_action(app, language):
    set_language(language)
    widget = PreviewWindow()
    widget.resize(800, 600)
    widget.show()
    app.processEvents()
    assert widget.launch.isVisible()
    if language == "fr":
        assert widget.launch.text() == "Lancer"
    text_width = widget.launch.fontMetrics().horizontalAdvance(widget.launch.text())
    assert widget.launch.width() >= text_width + 28
    assert widget.launch.accessibleName()
    assert widget.search.accessibleName()
    assert widget.tree.accessibleName()
    assert not widget.windowFlags() & QtCore.Qt.FramelessWindowHint
    widget.close()
    widget.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    set_language("en")


def test_detail_actions_are_keyboard_accessible_without_button_clutter(app, window):
    button_text = {
        button.text() for button in window.detail.findChildren(QtWidgets.QPushButton)
    }
    assert "Files" not in button_text
    assert "Uninstall" not in button_text
    assert window.description_label.isVisible()
    assert window.more_button.accessibleName() == "Game actions"
    assert [
        action.text()
        for action in window.game_menu.actions()
        if not action.isSeparator() and action.isVisible()
    ] == ["Launch options", "Uninstall"]
    assert all("Store" not in action.text() for action in window.game_menu.actions())
    window.more_button.setFocus()
    checked = []

    def inspect_menu():
        checked.append(window.game_menu.isVisible())
        QtTest.QTest.keyClick(window.game_menu, QtCore.Qt.Key_Escape)

    QtCore.QTimer.singleShot(30, inspect_menu)
    QtTest.QTest.keyClick(window.more_button, QtCore.Qt.Key_Space)
    QtTest.QTest.qWait(50)
    assert checked == [True]
    window.files_button.click()
    assert "Open files" in window.status.text()


def test_add_to_steam_visibility_follows_actual_shortcut_state(app, window):
    window.show_game(window.installed[0])
    assert window.add_steam_button.isVisible()
    window.show_game(window.installed[1])
    assert not window.add_steam_button.isVisible()
    window._steam_shortcut_state = lambda game: "unavailable"
    window.show_game(window.installed[0])
    assert not window.add_steam_button.isVisible()
    assert window.steam_status_action.isVisible()


def test_runtime_status_error_recovers_instead_of_abandoning_worker(
    app, window, monkeypatch
):
    from riftlift.main_window import Window
    from riftlift.util import RiftLiftError

    def fail(paths):
        raise RiftLiftError("Fixture backend unavailable")

    monkeypatch.setattr("riftlift.main_window.needs_setup", fail)
    Window._check_setup_status(window)
    Window._check_system_status(window)
    for _ in range(100):
        app.processEvents()
        if (
            "Fixture backend unavailable" in window.status.text()
            and "Fixture backend unavailable" in window.settings_page.status_text.text()
        ):
            break
        QtTest.QTest.qWait(10)
    assert "Fixture backend unavailable" in window.status.text()
    assert "Fixture backend unavailable" in window.settings_page.status_text.text()
    assert not window.setup_banner.isVisible()


def test_windows_optional_steam_sync_does_not_block_install_or_removal(
    app, window, monkeypatch
):
    from riftlift.game_ui import LocalGameDialog, StoreGameDialog
    from riftlift.main_window import Window

    monkeypatch.setattr("riftlift.game_ui.supports_steam_shortcuts", lambda: False)
    for dialog in (StoreGameDialog(window.paths, lambda: None), LocalGameDialog()):
        assert not dialog.steam.isChecked()
        assert not dialog.steam.isEnabled()
        assert "Windows" in dialog.steam.toolTip()
        dialog.close()
        dialog.deleteLater()

    removed = []
    monkeypatch.setattr("riftlift.main_window.supports_steam_shortcuts", lambda: False)
    monkeypatch.setattr("riftlift.main_window._themed_question", lambda *args: True)
    monkeypatch.setattr(
        "riftlift.main_window.remove", lambda *args: removed.append(args)
    )

    def forbidden(*args):
        raise AssertionError("unsupported Steam update must not run")

    monkeypatch.setattr("riftlift.main_window.sync_with_restart", forbidden)
    monkeypatch.setattr(
        window, "run_task", lambda label, operation, *a, **kw: operation()
    )
    Window.uninstall_selected(window)
    assert removed == [(window.paths, window.installed[0])]


def test_owned_windows_launch_stays_now_playing_through_poll_until_exit(
    app, window, monkeypatch
):
    from riftlift.main_window import Window

    game = window.installed[0]
    game.save(window.paths)
    monkeypatch.setattr("riftlift.main_window.running_launch", lambda paths: None)
    operations = []
    monkeypatch.setattr(
        window,
        "run_task",
        lambda label, operation, *a, **k: operations.append(operation),
    )
    monkeypatch.setattr("riftlift.main_window.launch", lambda *args: 0)
    Window.launch_game(window)
    assert window.now_playing_label.isVisible()
    assert game.name in window.now_playing_label.text()
    window._poll_running_game()
    assert window.now_playing_label.isVisible()
    assert not window.launch.isEnabled()
    operations[0]()
    window._poll_running_game()
    assert not window.now_playing_label.isVisible()
    assert window.launch.isEnabled()
