"""Portable Qt 6 UI used by :mod:`riftlift.gui`."""

from __future__ import annotations

import contextlib
import io
import threading
from typing import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from .config import Game, Paths, games
from .doctor import doctor
from .launch import launch
from .library import add
from .metadata import populate_game_metadata
from .runtime import login
from .steam import sync_with_restart

STYLE = """
QWidget{background:#0b1020;color:#f4f7ff;font:14px sans-serif}
QLabel#title{font-size:27px;font-weight:700} QLabel#game{background:transparent;font-size:30px;font-weight:700} QLabel#muted{background:transparent;color:#aeb8cd} QLabel#section{font-size:18px;font-weight:500}
QPushButton{background:#172238;border:1px solid #33415f;border-radius:16px;padding:7px 13px;min-height:20px} QPushButton:hover{background:#22304a} QPushButton:disabled{color:#66728b}
QPushButton#primary{background:#7c5cff;color:white;border:0;font-weight:700} QPushButton#primary:hover{background:#8b70ff}
QPushButton#nav{background:transparent;border:0;padding:8px 10px} QPushButton#nav:hover{background:#172238}
QPushButton#refresh{background:#172238;border:1px solid #33415f;border-radius:7px;padding:0;font-size:20px}
QListWidget{background:transparent;border:0;outline:0} QListWidget::item{background:#111a2c;border:1px solid #293650;border-radius:8px;margin:4px 0;padding:8px 10px} QListWidget::item:hover{background:#172238} QListWidget::item:selected{background:#172238;border:1px solid #7c5cff}
QLineEdit{background:#10182a;border:1px solid #33415f;border-radius:6px;padding:9px} QTextEdit{background:#080c17;color:#ccd5e8;border:1px solid #263552;border-radius:6px;font-family:monospace}
QCheckBox{spacing:8px} QCheckBox::indicator{width:16px;height:16px}
QSplitter::handle{background:#0b1020;width:12px}
"""


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
        head.addWidget(self.label("RiftLift", "title"))
        head.addStretch()
        self.check = self.button(
            "System",
            lambda: self.run_task("Checking your system", lambda: doctor(self.paths)),
        )
        self.check.setObjectName("nav")
        self.signin = self.button(
            "Sign In",
            lambda: self.run_task("Opening Meta sign-in", lambda: login(self.paths)),
        )
        self.signin.setObjectName("nav")
        self.addbtn = self.button("Add Game", self.add_dialog, True)
        for b in (self.check, self.signin, self.addbtn):
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
        refresh = self.button("⟳", self.refresh)
        refresh.setObjectName("refresh")
        refresh.setToolTip("Refresh library")
        refresh.setFixedSize(34, 34)
        lh.addWidget(refresh)
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
        self.launch = self.button("Launch in VR", self.launch_game, True)
        actions.addWidget(self.launch)
        actions.addWidget(self.button("Files", self.open_folder))
        actions.addWidget(self.button("Store", self.open_store))
        self.metadata = self.button("Refresh Info", self.refresh_metadata)
        actions.addWidget(self.metadata)
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
        self.meta.setText(
            " • ".join(
                x
                for x in (game.developer, game.version, ", ".join(game.genres[:2]))
                if x
            )
            or f"Meta app {game.app_id}"
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

    def refresh_metadata(self):
        if g := self.game():
            self.run_task(
                f"Refreshing {g.name}",
                lambda: populate_game_metadata(self.paths, g, refresh=True),
                refresh=g.slug,
            )

    def open_folder(self):
        if g := self.game():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(g.directory))

    def open_store(self):
        if g := self.game():
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
        l.addWidget(
            self.label(
                "Paste the URL of a PC VR game you own on the Meta store.", "muted"
            )
        )
        l.addWidget(self.label("Meta store URL or app ID", "section"))
        entry = QtWidgets.QLineEdit()
        entry.setPlaceholderText("https://www.meta.com/experiences/…")
        l.addWidget(entry)
        steam = QtWidgets.QCheckBox("Add to Steam when finished")
        steam.setChecked(True)
        l.addWidget(steam)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        submit = buttons.addButton(
            "Download game", QtWidgets.QDialogButtonBox.AcceptRole
        )
        submit.setObjectName("primary")
        buttons.rejected.connect(d.reject)
        l.addWidget(buttons)

        def accept():
            value = entry.text().strip()
            if not value:
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

    def run_task(self, label, operation, success="Done", refresh=False):
        if self.busy:
            self.status.setText("Another operation is already running")
            return
        self.busy = True
        self.status.setText(label + "…")
        self.addbtn.setEnabled(False)

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
    return app.exec()
