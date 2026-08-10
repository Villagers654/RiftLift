import os
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from riftlift.auth_browser import Browser
from riftlift.auth_ui import AuthDialog
from riftlift.cli import parser
from riftlift.config import Game, Paths
from riftlift.gui_qt import Window, is_valid_rift_store_url
from riftlift.metadata import CatalogMetadata
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
    assert not is_valid_rift_store_url("123456789")
    assert not is_valid_rift_store_url(
        "https://www.meta.com/experiences/quest/vader-immortal/123456789/"
    )
    assert not is_valid_rift_store_url(
        "https://example.com/experiences/pcvr/vader-immortal/123456789/"
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
        "System",
        "Sign In",
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
    )

    window.show_game(steam)
    assert window.store_link.text() == "Open in Steam ↗"
    window.show_game(rift)
    assert window.store_link.text() == "Open in Rift Store ↗"

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
            return None

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
    session = SimpleNamespace(
        login_url="https://auth.meta.com/native_sso/confirm",
        callback_ready=lambda: True,
    )
    monkeypatch.setattr(
        "riftlift.auth_ui.MetaAuthSession.begin", lambda _paths: session
    )

    def complete(login_paths, _session):
        target = login_paths.config / "meta-access-token"
        target.write_text("FRL" + "a" * 176)

    monkeypatch.setattr("riftlift.auth_ui.complete_browser_login", complete)
    dialog = AuthDialog(paths)

    dialog.start()
    assert wait_until(app, lambda: dialog.pending is not None and dialog.pending.done())
    dialog.check_login()
    dialog.check_login()
    assert wait_until(app, lambda: dialog.pending is not None and dialog.pending.done())
    dialog.check_login()

    assert dialog.completed
    assert stopped
    assert dialog.status.text() == "Signed in. Returning to RiftLift…"
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
    monkeypatch.setattr("riftlift.gui_qt.LINK_VALIDATION_DELAY_MS", 0)
    monkeypatch.setattr(
        "riftlift.gui_qt.fetch_catalog_metadata", lambda _app_id: catalog_game()
    )
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
    monkeypatch.setattr("riftlift.gui_qt.LINK_VALIDATION_DELAY_MS", 0)

    def missing(_app_id: str):
        raise RiftLiftError("Meta's store page has no catalog metadata for app 123")

    monkeypatch.setattr("riftlift.gui_qt.fetch_catalog_metadata", missing)
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
