"""Offline fixtures for the actual replacement Window. No account/runtime services start.

Run with ``python -m riftlift.gui --fixture --capture path.png``.
The fixture uses the default Window and substitutes only external operations.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from .config import Game, Paths
from .entitlements import OwnedApp
from .i18n import current_language, set_language
from .main_window import Window
from .metadata import CatalogMetadata


class PreviewWindow(Window):
    """Fixture host: actions change only in-memory preview state."""

    def __init__(self, state="installed"):
        root = (
            Path(__file__).resolve().parents[3]
            / "review"
            / "ui-fixtures"
            / str(uuid.uuid4())
        )
        paths = Paths(
            *(
                root / name
                for name in ("data", "cache", "config", "games", "prefix", "tools")
            )
        )
        paths.create()
        (paths.config / "language").write_text(current_language(), encoding="utf-8")
        super().__init__(paths, start_services=False)
        self.state = state
        self.actions = []
        self.setWindowTitle("RiftLift · offline UI preview")
        self._populate()
        self.signin.setText("Demo account" if state != "signed-out" else "Sign In")
        self.status.setText("Offline preview")
        if state == "signed-out":
            self.stack.setCurrentIndex(0)
        elif state == "loading":
            self.status.setText("Offline preview · loading owned library…")
            self.meta_skeleton.show()
            self.description_label.hide()
            self.description_skeleton.show()
        elif state == "error":
            self.status.setText(
                "Preview: connection interrupted. Refresh to try again. Your library is kept."
            )

    def _populate(self):
        if self.state == "signed-out":
            self._render_tree()
            return
        self.installed = [
            Game(
                "aster-drift",
                "Aster Drift",
                "1",
                "fixture",
                str(self.paths.games),
                "fixture.exe",
                [],
                version="1.2",
                source="meta",
                description_lang=current_language(),
                description="Chart a path through a luminous expanse. Explore distant observatories, trace forgotten signals, and find your way home.",
                artwork={"icon": str(Path(__file__).with_name("assets") / "mark.svg")},
            ),
            Game(
                "signal-reach",
                "Signal Reach",
                "2",
                "fixture",
                str(self.paths.games),
                "fixture.exe",
                [],
                version="1.4",
                source="meta",
                description_lang=current_language(),
                description="Follow the signal. Discover what waits across the divide.",
                artwork={"icon": str(Path(__file__).with_name("assets") / "mark.svg")},
            ),
        ]
        self.owned = [
            OwnedApp("3", "Tidal Workshop", "tidal-workshop"),
            OwnedApp("4", "Orbit Garden", "orbit-garden"),
        ]
        self._owned_icons = {
            app.app_id: str(Path(__file__).with_name("assets") / "mark.svg")
            for app in self.owned
        }
        self._owned_loaded = True
        self._render_tree()
        if self.state == "owned":
            self.tree.setCurrentItem(self._owned_category.child(0))

    def _steam_shortcut_state(self, game):
        return "present" if game.slug == "signal-reach" else "absent"

    def _load_owned_detail(self, app, *, refresh=False):
        return CatalogMetadata(
            app.name,
            "",
            "Build something extraordinary on the edge of the ocean.",
            "",
            "",
            [],
            "",
        ), ""

    def _confirm_language_change(self, code):
        return False

    def _check_system_status(self):
        self.settings_page.set_status(True, "Offline fixture")

    def _run_system_check(self):
        self._action("Diagnostics")

    def _action(self, action):
        self.actions.append(action)
        self.status.setText(
            f"Offline preview · {action} selected · no backend operation"
        )

    def show_auth(self):
        self._action("Sign in")

    def _run_setup(self):
        self._action("Runtime recovery")

    def refresh_all(self):
        self._action("Refresh")

    def steam_dialog(self):
        self._action("Steam games")

    def add_dialog(self):
        self._action("Add game")

    def launch_game(self):
        self._action("Play")

    def install_owned(self):
        self._action("Download")

    def open_folder(self):
        self._action("Open files")

    def launch_options(self):
        self._action("Launch options")

    def open_store(self):
        self._action("Game information")

    def add_selected_to_steam(self):
        self._action("Add to Steam")

    def uninstall_selected(self):
        self._action("Uninstall")

    def show_activity(self):
        self._action("Activity")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state",
        choices=["installed", "owned", "signed-out", "loading", "error"],
        default="installed",
    )
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--language", choices=["en", "fr"], default="en")
    args = parser.parse_args(argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    set_language(args.language)
    window = PreviewWindow(args.state)
    if args.compact:
        window.resize(800, 600)
    window.show()
    if args.capture:

        def capture():
            args.capture.parent.mkdir(parents=True, exist_ok=True)
            if not window.grab().save(str(args.capture)):
                raise OSError(f"Could not save UI capture: {args.capture}")
            app.quit()

        QtCore.QTimer.singleShot(250, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
