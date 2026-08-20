"""Portable Qt 6 UI used by :mod:`riftlift.gui`."""

from __future__ import annotations

import contextlib
import io
import threading
from collections.abc import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from .auth import is_signed_in
from .auth_ui import AuthDialog
from .config import (
    Game,
    Paths,
    debug_logging_enabled,
    games,
    set_debug_logging,
)
from .doctor import doctor
from .game_ui import LocalGameDialog, StoreGameDialog
from .launch import launch
from .library import add, add_local
from .metadata import populate_game_metadata
from .playtime import playtime, playtime_label
from .steam import sync_with_restart
from .steam_oculus import add_steam_game
from .steam_ui import SteamGamesDialog
from .theme import STYLE
from .util import RiftLiftError


class Events(QtCore.QObject):
    output = QtCore.Signal(str)
    complete = QtCore.Signal(str, object, object, object)


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
        self.busy_label = ""
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

    def _build_header(self, outer: QtWidgets.QVBoxLayout) -> None:
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(self.label("RiftLift", "title"))
        header.addStretch()
        self.debug_logging = QtWidgets.QCheckBox("Debug logging")
        self.debug_logging.setChecked(debug_logging_enabled(self.paths))
        self.debug_logging.setToolTip(
            "Capture Proton, Wine XR/Steam/Vulkan, DXVK, VKD3D, loader, and "
            "crash diagnostics for future System reports. Storage is limited."
        )
        self.debug_logging.toggled.connect(self.set_debug_logging)
        header.addWidget(self.debug_logging)
        self.check = self.button(
            "System",
            lambda: self.run_task("Checking your system", lambda: doctor(self.paths)),
        )
        self.check.setObjectName("nav")
        self.signin = self.button(
            "Account" if is_signed_in(self.paths) else "Sign In", self.show_auth
        )
        self.signin.setObjectName("nav")
        self.steam_games = self.button("Steam Games", self.steam_dialog)
        self.steam_games.setObjectName("nav")
        self.addbtn = self.button("Add Game", self.add_dialog, True)
        for button in (self.check, self.signin, self.steam_games, self.addbtn):
            header.addWidget(button)
        outer.addLayout(header)
        outer.addSpacing(20)

    def _build_library(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel.setFixedWidth(280)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 19, 0, 0)
        layout.setSpacing(12)
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(self.label("Library", "section"))
        self.count = self.label("", "muted")
        heading.addWidget(self.count)
        heading.addStretch()
        self.refresh_button = self.button("⟳", self.refresh_library)
        self.refresh_button.setObjectName("refresh")
        self.refresh_button.setToolTip("Refresh library and game info")
        self.refresh_button.setFixedSize(34, 34)
        heading.addWidget(self.refresh_button)
        layout.addLayout(heading)
        self.library = QtWidgets.QListWidget()
        self.library.setIconSize(QtCore.QSize(40, 40))
        self.library.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.library.currentItemChanged.connect(self.selected)
        layout.addWidget(self.library, 1)
        return panel

    def _build_empty_state(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.addStretch()
        title = self.label("No Rift games yet", "game")
        title.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(title)
        hint = self.label(
            "Add an owned Meta Rift title to download it and make it ready for OpenXR.",
            "muted",
        )
        hint.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(hint)
        layout.addWidget(
            self.button("Add Game", self.add_dialog, True),
            alignment=QtCore.Qt.AlignCenter,
        )
        layout.addStretch()
        return panel

    def _build_game_detail(self) -> HeroPanel:
        detail = HeroPanel()
        detail.setObjectName("detail")
        layout = QtWidgets.QVBoxLayout(detail)
        layout.setContentsMargins(24, 24, 24, 24)
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
        layout.addLayout(info)
        return detail

    def _build_status_bar(self, outer: QtWidgets.QVBoxLayout) -> None:
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet("color:#263552")
        outer.addWidget(line)
        outer.addSpacing(20)
        bar = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status = self.label("Ready", "muted")
        layout.addWidget(self.status, 1)
        activity = self.button("View Activity", self.show_activity)
        activity.setObjectName("nav")
        layout.addWidget(activity)
        outer.addWidget(bar)

    def _build(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(20, 17, 20, 15)
        outer.setSpacing(0)
        self._build_header(outer)
        content = QtWidgets.QHBoxLayout()
        content.setSpacing(20)
        outer.addLayout(content, 1)
        content.addWidget(self._build_library())
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.stack = QtWidgets.QStackedWidget()
        rl.addWidget(self.stack)
        content.addWidget(right, 1)
        self.stack.addWidget(self._build_empty_state())
        self.detail = self._build_game_detail()
        self.stack.addWidget(self.detail)
        self._build_status_bar(outer)

    def set_debug_logging(self, enabled):
        set_debug_logging(self.paths, enabled)
        self.status.setText(
            "Debug logging enabled for future launches"
            if enabled
            else "Debug logging disabled"
        )

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
        details = [
            value
            for value in (game.developer, game.version, ", ".join(game.genres[:2]))
            if value
        ]
        if not details:
            details.append(
                "Local game" if game.source == "local" else f"Meta app {game.app_id}"
            )
        details.append(playtime_label(playtime(self.paths, game.slug)))
        self.meta.setText(" • ".join(details))
        self.detail.set_artwork(game.artwork.get("portrait", ""))

    def game(self):
        return next((g for g in self.installed if g.slug == self.slug), None)

    def launch_game(self):
        if g := self.game():
            self.run_task(
                f"Launching {g.name}",
                lambda: launch(self.paths, g, []),
                f"{g.name} closed",
                refresh=True,
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
        dialog = StoreGameDialog(self.local_dialog, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return

        def operation():
            game = add(self.paths, dialog.url)
            if dialog.sync_steam:
                sync_with_restart(self.paths)
            return game.slug

        self.run_task("Downloading and installing game", operation, refresh=True)

    def local_dialog(self):
        dialog = LocalGameDialog(self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return

        def operation():
            game = add_local(
                self.paths,
                dialog.executable,
                name=dialog.game_name,
                arguments=dialog.arguments,
                artwork=dialog.artwork,
            )
            if dialog.sync_steam:
                sync_with_restart(self.paths)
            return game.slug

        self.run_task("Adding local game", operation, refresh=True)

    def run_task(self, label, operation, success="Done", refresh=False):
        if self.busy:
            self.status.setText("Another operation is already running")
            return
        self.busy = True
        self.busy_label = label
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
        self.busy_label = ""
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
                    else refresh
                    if isinstance(refresh, str)
                    else self.slug
                )

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
            self.status.setText(
                f"{self.busy_label} is still running; minimize RiftLift instead"
            )
            return
        super().closeEvent(event)

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
        layout = QtWidgets.QVBoxLayout(d)
        layout.addWidget(self.label("Activity", "game"))
        view = QtWidgets.QTextEdit(readOnly=True)
        view.setPlainText(self.log or "No activity yet.\n")
        layout.addWidget(view)
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
