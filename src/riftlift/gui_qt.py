"""Portable Qt 6 UI used by :mod:`riftlift.gui`."""

from __future__ import annotations

import contextlib
import io
import re
import threading
from typing import Callable
from urllib.parse import urlparse

from PySide6 import QtCore, QtGui, QtWidgets

from .auth import is_signed_in
from .auth_ui import AuthDialog
from .config import Game, Paths, games
from .doctor import doctor
from .launch import launch
from .library import add, add_local
from .metadata import fetch_catalog_metadata, populate_game_metadata
from .steam import sync_with_restart
from .steam_oculus import add_steam_game
from .steam_ui import SteamGamesDialog
from .theme import STYLE
from .util import RiftLiftError

LINK_VALIDATION_DELAY_MS = 350


def rift_store_app_id(value: str) -> str | None:
    """Return the app ID from an exact Meta Rift/PCVR product URL."""
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    match = re.fullmatch(r"/experiences/pcvr/[^/]+/(?P<app_id>\d{8,})/?", parsed.path)
    if not (
        parsed.scheme.lower() == "https"
        and host in {"meta.com", "www.meta.com"}
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and match
    ):
        return None
    return match.group("app_id")


def is_valid_rift_store_url(value: str) -> bool:
    """Return whether *value* has the exact shape of a Rift product URL."""
    return rift_store_app_id(value) is not None


class Events(QtCore.QObject):
    output = QtCore.Signal(str)
    complete = QtCore.Signal(str, object, object, object)


class LinkValidationEvents(QtCore.QObject):
    complete = QtCore.Signal(int, str, object, object)


class Output(io.TextIOBase):
    def __init__(self, emit: Callable[[str], None]):
        self.emit = emit

    def write(self, value: str) -> int:
        if value:
            self.emit(value)
        return len(value)

    def flush(self) -> None:
        pass


class HeroPanel(QtWidgets.QWidget):
    """Selected-game artwork with a readable, content-first text area."""

    def __init__(self):
        super().__init__()
        self.hero = QtGui.QPixmap()

    def set_artwork(self, path: str) -> None:
        self.hero = QtGui.QPixmap(path)
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        clip = QtGui.QPainterPath()
        clip.addRoundedRect(QtCore.QRectF(self.rect()), 10, 10)
        painter.setClipPath(clip)
        painter.fillRect(self.rect(), QtGui.QColor("#0b1020"))
        if not self.hero.isNull():
            artwork_rect = QtCore.QRect(
                self.width() - int(self.width() * 0.54),
                0,
                int(self.width() * 0.54),
                self.height(),
            )
            artwork = self.hero.scaled(
                artwork_rect.size(),
                QtCore.Qt.KeepAspectRatioByExpanding,
                QtCore.Qt.SmoothTransformation,
            )
            source = QtCore.QRect(
                (artwork.width() - artwork_rect.width()) // 2,
                (artwork.height() - artwork_rect.height()) // 2,
                artwork_rect.width(),
                artwork_rect.height(),
            )
            painter.drawPixmap(artwork_rect, artwork, source)

        horizontal = QtGui.QLinearGradient(0, 0, self.width(), 0)
        horizontal.setColorAt(0.0, QtGui.QColor(11, 16, 32, 250))
        horizontal.setColorAt(0.48, QtGui.QColor(11, 16, 32, 235))
        horizontal.setColorAt(0.72, QtGui.QColor(11, 16, 32, 100))
        horizontal.setColorAt(1.0, QtGui.QColor(11, 16, 32, 12))
        painter.fillRect(self.rect(), horizontal)

        vertical = QtGui.QLinearGradient(0, self.height() * 0.45, 0, self.height())
        vertical.setColorAt(0.0, QtGui.QColor(11, 16, 32, 0))
        vertical.setColorAt(1.0, QtGui.QColor(11, 16, 32, 235))
        painter.fillRect(self.rect(), vertical)


class Window(QtWidgets.QMainWindow):
    def __init__(self, paths: Paths | None = None):
        super().__init__()
        self.paths = paths or Paths.defaults()
        self.installed: list[Game] = []
        self.slug: str | None = None
        self.busy = False
        self.log = ""
        self.log_views: list[QtWidgets.QTextEdit] = []
        self.events = Events()
        self.events.output.connect(self._append_log)
        self.events.complete.connect(self._finish)
        self.setWindowTitle("RiftLift")
        self.resize(1024, 637)
        self.setMinimumSize(1024, 637)
        self.setStyleSheet(STYLE)
        self._build()
        self.refresh()

    def label(self, text="", name=""):
        widget = QtWidgets.QLabel(text)
        widget.setObjectName(name)
        return widget

    def button(self, text, callback, primary=False):
        widget = QtWidgets.QPushButton(text)
        widget.setObjectName("primary" if primary else "")
        widget.clicked.connect(callback)
        return widget

    def _build(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(20, 17, 20, 15)
        outer.setSpacing(0)
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(self.label("RiftLift", "title"))
        head.addStretch()
        self.check = self.button(
            "System",
            lambda: self.run_task("Checking your system", lambda: doctor(self.paths)),
        )
        self.check.setObjectName("nav")
        self.signin = self.button(
            "Account" if is_signed_in(self.paths) else "Sign In",
            self.show_auth,
        )
        self.signin.setObjectName("nav")
        self.steam_games = self.button("Steam Games", self.steam_dialog)
        self.steam_games.setObjectName("nav")
        self.addbtn = self.button("Add Game", self.add_dialog, True)
        for b in (self.check, self.signin, self.steam_games, self.addbtn):
            head.addWidget(b)
        outer.addLayout(head)
        outer.addSpacing(20)
        content = QtWidgets.QHBoxLayout()
        content.setSpacing(20)
        outer.addLayout(content, 1)
        left = QtWidgets.QWidget()
        left.setFixedWidth(280)
        ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(0, 19, 0, 0)
        ll.setSpacing(12)
        lh = QtWidgets.QHBoxLayout()
        lh.addWidget(self.label("Library", "section"))
        self.count = self.label("", "muted")
        lh.addWidget(self.count)
        lh.addStretch()
        self.refresh_button = self.button("⟳", self.refresh_library)
        self.refresh_button.setObjectName("refresh")
        self.refresh_button.setToolTip("Refresh library and game info")
        self.refresh_button.setFixedSize(34, 34)
        lh.addWidget(self.refresh_button)
        ll.addLayout(lh)
        self.library = QtWidgets.QListWidget()
        self.library.setIconSize(QtCore.QSize(40, 40))
        self.library.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.library.currentItemChanged.connect(self.selected)
        ll.addWidget(self.library, 1)
        content.addWidget(left)
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.stack = QtWidgets.QStackedWidget()
        rl.addWidget(self.stack)
        content.addWidget(right, 1)
        empty = QtWidgets.QWidget()
        el = QtWidgets.QVBoxLayout(empty)
        el.addStretch()
        title = self.label("No Rift games yet", "game")
        title.setAlignment(QtCore.Qt.AlignCenter)
        el.addWidget(title)
        hint = self.label(
            "Add an owned Meta Rift title to download it and make it ready for OpenXR.",
            "muted",
        )
        hint.setAlignment(QtCore.Qt.AlignCenter)
        el.addWidget(hint)
        first = self.button("Add Game", self.add_dialog, True)
        el.addWidget(first, alignment=QtCore.Qt.AlignCenter)
        el.addStretch()
        self.stack.addWidget(empty)
        detail = HeroPanel()
        detail.setObjectName("detail")
        dl = QtWidgets.QVBoxLayout(detail)
        dl.setContentsMargins(24, 24, 24, 24)
        info = QtWidgets.QVBoxLayout()
        info.setSpacing(0)
        info.addSpacing(128)
        self.game_name = self.label("", "game")
        self.game_name.setWordWrap(True)
        self.game_name.setMaximumWidth(340)
        info.addWidget(self.game_name)
        info.addSpacing(8)
        self.meta = self.label("", "muted")
        self.meta.setWordWrap(True)
        info.addWidget(self.meta)
        info.addSpacing(14)
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(10)
        self.launch = self.button("Launch in VR", self.launch_game, True)
        actions.addWidget(self.launch)
        actions.addWidget(self.button("Files", self.open_folder))
        self.store_link = self.button("Open in Rift Store ↗", self.open_store)
        self.store_link.setObjectName("link")
        self.store_link.setCursor(QtCore.Qt.PointingHandCursor)
        actions.addWidget(self.store_link, alignment=QtCore.Qt.AlignVCenter)
        actions.addStretch()
        info.addLayout(actions)
        info.addStretch()
        dl.addLayout(info)
        self.detail = detail
        self.stack.addWidget(detail)
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet("color:#263552")
        outer.addWidget(line)
        outer.addSpacing(20)
        bar = QtWidgets.QWidget()
        bl = QtWidgets.QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        self.status = self.label("Ready", "muted")
        bl.addWidget(self.status, 1)
        activity = self.button("View Activity", self.show_activity)
        activity.setObjectName("nav")
        bl.addWidget(activity)
        outer.addWidget(bar)

    def refresh(self, preferred=None):
        preferred = preferred or self.slug
        self.installed = games(self.paths)
        self.library.blockSignals(True)
        self.library.clear()
        chosen = 0
        for i, game in enumerate(self.installed):
            item = QtWidgets.QListWidgetItem(game.name)
            item.setToolTip(
                game.name + (f"\nVersion {game.version}" if game.version else "")
            )
            item.setData(QtCore.Qt.UserRole, game.slug)
            item.setSizeHint(QtCore.QSize(0, 70))
            icon = QtGui.QIcon(game.artwork.get("icon", ""))
            if not icon.isNull():
                item.setIcon(icon)
            self.library.addItem(item)
            if game.slug == preferred:
                chosen = i
        self.library.blockSignals(False)
        n = len(self.installed)
        self.count.setText(f"{n} game" + ("" if n == 1 else "s"))
        if n:
            self.library.setCurrentRow(chosen)
            self.show_game(self.installed[chosen])
        else:
            self.slug = None
            self.stack.setCurrentIndex(0)

    def show_auth(self):
        dialog = AuthDialog(self.paths, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted and dialog.completed:
            self.status.setText("Signed in to Meta")
        elif not is_signed_in(self.paths):
            self.status.setText("Signed out of Meta")
        self.signin.setText("Account" if is_signed_in(self.paths) else "Sign In")

    def steam_dialog(self):
        dialog = SteamGamesDialog(self.paths, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted or dialog.selected_game is None:
            return
        selected = dialog.selected_game

        def operation():
            game = add_steam_game(self.paths, selected)
            try:
                populate_game_metadata(self.paths, game, refresh=True)
            except RiftLiftError as error:
                print(f"warning: Steam catalog metadata was not available: {error}")
            return game.slug

        self.run_task(
            f"Adding {selected.name} from Steam",
            operation,
            f"Added {selected.name} from Steam",
            refresh=True,
        )

    def selected(self, item, _old):
        if item and (
            game := next(
                (g for g in self.installed if g.slug == item.data(QtCore.Qt.UserRole)),
                None,
            )
        ):
            self.show_game(game)

    def show_game(self, game):
        self.slug = game.slug
        self.stack.setCurrentIndex(1)
        self.game_name.setText(game.name)
        self.store_link.setVisible(game.source != "local")
        self.store_link.setText(
            "Open in Steam ↗" if game.source == "steam" else "Open in Rift Store ↗"
        )
        self.meta.setText(
            " • ".join(
                x
                for x in (game.developer, game.version, ", ".join(game.genres[:2]))
                if x
            )
            or ("Local game" if game.source == "local" else f"Meta app {game.app_id}")
        )
        self.detail.set_artwork(game.artwork.get("portrait", ""))

    def game(self):
        return next((g for g in self.installed if g.slug == self.slug), None)

    def launch_game(self):
        if g := self.game():
            self.run_task(
                f"Launching {g.name}",
                lambda: launch(self.paths, g, []),
                f"{g.name} closed",
            )

    def refresh_library(self):
        installed = games(self.paths)
        if not installed:
            self.refresh()
            return
        preferred = self.slug

        def operation():
            for game in installed:
                populate_game_metadata(self.paths, game, refresh=True)
            return preferred

        self.run_task(
            "Refreshing library", operation, "Library refreshed", refresh=True
        )

    def open_folder(self):
        if g := self.game():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(g.directory))

    def open_store(self):
        if (g := self.game()) and g.source != "local":
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl(
                    g.store_url or f"https://www.meta.com/experiences/pcvr/{g.app_id}/"
                )
            )

    def add_dialog(self):
        d = QtWidgets.QDialog(self)
        d.setWindowTitle("Add a Rift game")
        d.setMinimumWidth(560)
        d.setStyleSheet(STYLE)
        l = QtWidgets.QVBoxLayout(d)
        l.setContentsMargins(26, 24, 26, 24)
        l.setSpacing(12)
        l.addWidget(self.label("Add to your library", "game"))
        local = self.button(
            "Add a local game…", lambda: (d.reject(), self.local_dialog())
        )
        local.setObjectName("link")
        l.addWidget(local, alignment=QtCore.Qt.AlignLeft)
        l.addWidget(self.label("Meta Rift store URL", "section"))
        entry = QtWidgets.QLineEdit()
        entry.setPlaceholderText("https://www.meta.com/experiences/pcvr/…")
        l.addWidget(entry)
        validation = self.label(
            "Paste a valid Meta Rift store link to continue.", "muted"
        )
        l.addWidget(validation)
        steam = QtWidgets.QCheckBox("Add to Steam when finished")
        steam.setChecked(True)
        l.addWidget(steam)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setIcon(QtGui.QIcon())
        submit = buttons.addButton("Install", QtWidgets.QDialogButtonBox.AcceptRole)
        submit.setObjectName("primary")
        submit.setEnabled(False)
        buttons.rejected.connect(d.reject)
        l.addWidget(buttons)

        validation_timer = QtCore.QTimer(d)
        validation_timer.setSingleShot(True)
        validation_events = LinkValidationEvents(d)
        generation = 0
        verified_value = ""

        def finish_validation(token: int, value: str, metadata, error):
            nonlocal verified_value
            if token != generation or value != entry.text().strip():
                return
            if error is not None:
                message = str(error)
                validation.setText(
                    "This Rift store game could not be found."
                    if "has no catalog metadata" in message
                    else "Could not verify this link. Check your connection."
                )
                return
            if not metadata or not metadata.name.strip():
                validation.setText("This Rift store game could not be found.")
                return
            verified_value = value
            submit.setEnabled(True)
            validation.setText(f"Ready to install {metadata.name}.")

        validation_events.complete.connect(finish_validation)

        def check_catalog():
            token = generation
            value = entry.text().strip()
            app_id = rift_store_app_id(value)
            if app_id is None:
                return

            def worker():
                try:
                    metadata = fetch_catalog_metadata(app_id)
                    validation_events.complete.emit(token, value, metadata, None)
                except Exception as error:
                    validation_events.complete.emit(token, value, None, error)

            threading.Thread(
                target=worker, daemon=True, name="riftlift-link-validation"
            ).start()

        validation_timer.timeout.connect(check_catalog)

        def validate(value: str):
            nonlocal generation, verified_value
            generation += 1
            verified_value = ""
            validation_timer.stop()
            submit.setEnabled(False)
            if not is_valid_rift_store_url(value):
                validation.setText("Paste a valid Meta Rift store link to continue.")
                return
            validation.setText("Checking Rift store link…")
            validation_timer.start(LINK_VALIDATION_DELAY_MS)

        entry.textChanged.connect(validate)

        def accept():
            value = entry.text().strip()
            if value != verified_value:
                entry.setFocus()
                return
            sync = steam.isChecked()
            d.accept()

            def operation():
                game = add(self.paths, value)
                if sync:
                    sync_with_restart(self.paths)
                return game.slug

            self.run_task("Downloading and installing game", operation, refresh=True)

        submit.clicked.connect(accept)
        entry.returnPressed.connect(accept)
        d.exec()

    def local_dialog(self):
        d = QtWidgets.QDialog(self)
        d.setWindowTitle("Add a local VR game")
        d.setMinimumWidth(600)
        d.setStyleSheet(STYLE)
        layout = QtWidgets.QVBoxLayout(d)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(12)
        layout.addWidget(self.label("Add a local VR game", "game"))
        layout.addWidget(
            self.label(
                "Choose an installed Windows VR game. RiftLift leaves its files in place.",
                "muted",
            )
        )

        layout.addWidget(self.label("Game executable", "section"))
        executable_row = QtWidgets.QHBoxLayout()
        executable = QtWidgets.QLineEdit()
        executable.setPlaceholderText("/path/to/game.exe")
        browse = self.button(
            "Browse…", lambda: choose_file(executable, "Windows games (*.exe)")
        )
        executable_row.addWidget(executable, 1)
        executable_row.addWidget(browse)
        layout.addLayout(executable_row)

        layout.addWidget(self.label("Name", "section"))
        name = QtWidgets.QLineEdit()
        name.setPlaceholderText("Filled from the executable")
        layout.addWidget(name)
        layout.addWidget(self.label("Launch arguments (optional)", "section"))
        arguments = QtWidgets.QLineEdit()
        layout.addWidget(arguments)
        layout.addWidget(self.label("Cover image (optional)", "section"))
        artwork_row = QtWidgets.QHBoxLayout()
        artwork = QtWidgets.QLineEdit()
        artwork.setPlaceholderText("PNG, JPEG, or WebP")
        artwork_browse = self.button(
            "Browse…",
            lambda: choose_file(artwork, "Images (*.png *.jpg *.jpeg *.webp)"),
        )
        artwork_row.addWidget(artwork, 1)
        artwork_row.addWidget(artwork_browse)
        layout.addLayout(artwork_row)

        steam = QtWidgets.QCheckBox("Add to Steam when finished")
        steam.setChecked(True)
        layout.addWidget(steam)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setIcon(QtGui.QIcon())
        submit = buttons.addButton("Add", QtWidgets.QDialogButtonBox.AcceptRole)
        submit.setObjectName("primary")
        submit.setEnabled(False)
        buttons.rejected.connect(d.reject)
        layout.addWidget(buttons)

        def choose_file(target: QtWidgets.QLineEdit, file_filter: str):
            selected, _ = QtWidgets.QFileDialog.getOpenFileName(
                d, "Choose a file", target.text(), file_filter
            )
            if selected:
                target.setText(selected)

        def executable_changed(value: str):
            path = QtCore.QFileInfo(value.strip())
            submit.setEnabled(path.isFile() and path.suffix().casefold() == "exe")
            if path.isFile() and not name.text().strip():
                name.setText(path.completeBaseName())

        executable.textChanged.connect(executable_changed)

        def accept():
            path = executable.text().strip()
            if not submit.isEnabled():
                executable.setFocus()
                return
            sync = steam.isChecked()
            game_name = name.text().strip() or None
            game_arguments = arguments.text().strip() or None
            game_artwork = artwork.text().strip() or None
            d.accept()

            def operation():
                game = add_local(
                    self.paths,
                    path,
                    name=game_name,
                    arguments=game_arguments,
                    artwork=game_artwork,
                )
                if sync:
                    sync_with_restart(self.paths)
                return game.slug

            self.run_task("Adding local game", operation, refresh=True)

        submit.clicked.connect(accept)
        d.exec()

    def run_task(self, label, operation, success="Done", refresh=False):
        if self.busy:
            self.status.setText("Another operation is already running")
            return
        self.busy = True
        self.status.setText(label + "…")
        self.addbtn.setEnabled(False)
        self.refresh_button.setEnabled(False)

        def worker():
            try:
                with (
                    contextlib.redirect_stdout(Output(self.events.output.emit)),
                    contextlib.redirect_stderr(Output(self.events.output.emit)),
                ):
                    result = operation()
                self.events.complete.emit(success, result, refresh, None)
            except Exception as error:
                self.events.complete.emit("", None, False, error)

        threading.Thread(target=worker, daemon=True, name="riftlift-operation").start()

    def _finish(self, message, result, refresh, error):
        self.busy = False
        self.addbtn.setEnabled(True)
        self.refresh_button.setEnabled(True)
        if error:
            self.status.setText(str(error))
            self._append_log(f"\nError: {error}\n")
            QtWidgets.QMessageBox.critical(self, "RiftLift", str(error))
        else:
            self.status.setText(message)
            if refresh:
                self.refresh(
                    result
                    if isinstance(result, str)
                    else refresh if isinstance(refresh, str) else self.slug
                )

    def _append_log(self, value):
        self.log = (self.log + value)[-30000:]
        for view in list(self.log_views):
            if not view.isVisible():
                self.log_views.remove(view)
            else:
                view.setPlainText(self.log)
                view.moveCursor(QtGui.QTextCursor.End)

    def show_activity(self):
        d = QtWidgets.QDialog(self)
        d.setWindowTitle("RiftLift activity")
        d.resize(800, 440)
        d.setStyleSheet(STYLE)
        l = QtWidgets.QVBoxLayout(d)
        l.addWidget(self.label("Activity", "game"))
        view = QtWidgets.QTextEdit(readOnly=True)
        view.setPlainText(self.log or "No activity yet.\n")
        l.addWidget(view)
        self.log_views.append(view)
        d.finished.connect(
            lambda: self.log_views.remove(view) if view in self.log_views else None
        )
        d.exec()


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("RiftLift")
    app.setStyle("Fusion")
    window = Window()
    app._riftlift_window = window
    window.show()
    return app.exec()
