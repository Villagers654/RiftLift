"""Portable Qt 6 UI used by :mod:`riftlift.gui`."""

from __future__ import annotations

import contextlib
import io
import threading
from pathlib import Path
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
QWidget{background:#0b1020;color:#f4f7ff;font:14px sans-serif} QFrame#panel{background:#121a2c;border:1px solid #263552;border-radius:14px}
QLabel#title{font-size:29px;font-weight:800} QLabel#game{font-size:23px;font-weight:800} QLabel#muted{color:#9eabc3} QLabel#eye{color:#43d9a3;font-size:11px;font-weight:800}
QLabel#hero{background:#172238;border-radius:10px;color:#9eabc3;font-size:34px;font-weight:800}
QPushButton{background:#172238;border:1px solid #263552;border-radius:8px;padding:9px 14px;font-weight:600} QPushButton:hover{background:#263552} QPushButton:disabled{color:#66728b}
QPushButton#primary{background:#7c5cff;color:white;border:0;padding:10px 17px;font-weight:700} QPushButton#primary:hover{background:#9178ff}
QListWidget{background:transparent;border:0;outline:0} QListWidget::item{border-radius:8px;padding:12px 10px;margin:2px} QListWidget::item:hover{background:#172238} QListWidget::item:selected{background:#7c5cff}
QLineEdit,QSpinBox{background:#172238;border:1px solid #263552;border-radius:7px;padding:9px} QTextEdit{background:#080c17;color:#ccd5e8;border:1px solid #263552;border-radius:8px;font-family:monospace}
QSplitter::handle{background:#0b1020;width:10px}
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
        self.resize(1120, 720)
        self.setMinimumSize(900, 600)
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

    def panel(self):
        widget = QtWidgets.QFrame()
        widget.setObjectName("panel")
        return widget

    def _build(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(24, 22, 24, 20)
        outer.setSpacing(16)
        head = QtWidgets.QHBoxLayout()
        brand = QtWidgets.QVBoxLayout()
        brand.addWidget(self.label("RiftLift", "title"))
        brand.addWidget(
            self.label("Your Meta Rift library, lifted into Linux OpenXR", "muted")
        )
        head.addLayout(brand, 1)
        self.check = self.button(
            "Check system",
            lambda: self.run_task("Checking your system", lambda: doctor(self.paths)),
        )
        self.signin = self.button(
            "Sign in",
            lambda: self.run_task("Opening Meta sign-in", lambda: login(self.paths)),
        )
        self.addbtn = self.button("＋  Add game", self.add_dialog, True)
        for b in (self.check, self.signin, self.addbtn):
            head.addWidget(b)
        outer.addLayout(head)
        split = QtWidgets.QSplitter()
        split.setChildrenCollapsible(False)
        outer.addWidget(split, 1)
        left = self.panel()
        ll = QtWidgets.QVBoxLayout(left)
        lh = QtWidgets.QHBoxLayout()
        lh.addWidget(self.label("LIBRARY", "eye"))
        lh.addStretch()
        self.count = self.label("", "muted")
        lh.addWidget(self.count)
        ll.addLayout(lh)
        self.library = QtWidgets.QListWidget()
        self.library.currentItemChanged.connect(self.selected)
        ll.addWidget(self.library, 1)
        ll.addWidget(self.button("↻  Refresh library", self.refresh))
        split.addWidget(left)
        right = self.panel()
        rl = QtWidgets.QVBoxLayout(right)
        self.stack = QtWidgets.QStackedWidget()
        rl.addWidget(self.stack)
        split.addWidget(right)
        split.setSizes([300, 760])
        empty = QtWidgets.QWidget()
        el = QtWidgets.QVBoxLayout(empty)
        el.addStretch()
        diamond = self.label("◇", "title")
        diamond.setStyleSheet("color:#7c5cff;font-size:60px")
        diamond.setAlignment(QtCore.Qt.AlignCenter)
        el.addWidget(diamond)
        title = self.label("Your VR library starts here", "game")
        title.setAlignment(QtCore.Qt.AlignCenter)
        el.addWidget(title)
        hint = self.label(
            "Add an owned Meta Rift title to download it and make it ready for OpenXR.",
            "muted",
        )
        hint.setAlignment(QtCore.Qt.AlignCenter)
        el.addWidget(hint)
        first = self.button("Add your first game", self.add_dialog, True)
        el.addWidget(first, alignment=QtCore.Qt.AlignCenter)
        el.addStretch()
        self.stack.addWidget(empty)
        detail = QtWidgets.QWidget()
        dl = QtWidgets.QVBoxLayout(detail)
        dl.setContentsMargins(0, 0, 0, 0)
        self.hero = self.label("RIFTLIFT", "hero")
        self.hero.setAlignment(QtCore.Qt.AlignCenter)
        self.hero.setMinimumHeight(215)
        self.hero.setMaximumHeight(245)
        dl.addWidget(self.hero)
        self.game_name = self.label("", "game")
        dl.addWidget(self.game_name)
        self.meta = self.label("", "muted")
        dl.addWidget(self.meta)
        self.desc = self.label("", "muted")
        self.desc.setWordWrap(True)
        self.desc.setAlignment(QtCore.Qt.AlignTop)
        dl.addWidget(self.desc, 1)
        actions = QtWidgets.QHBoxLayout()
        self.launch = self.button("▶  Launch in VR", self.launch_game, True)
        actions.addWidget(self.launch)
        actions.addWidget(self.button("Open folder", self.open_folder))
        actions.addWidget(self.button("Store page", self.open_store))
        actions.addStretch()
        self.metadata = self.button("Refresh metadata", self.refresh_metadata)
        actions.addWidget(self.metadata)
        dl.addLayout(actions)
        self.stack.addWidget(detail)
        bar = QtWidgets.QFrame()
        bar.setStyleSheet("background:#172238;border-radius:10px")
        bl = QtWidgets.QHBoxLayout(bar)
        bl.setContentsMargins(13, 7, 9, 7)
        self.dot = self.label("●")
        self.dot.setStyleSheet("color:#43d9a3")
        self.status = self.label("Ready", "muted")
        bl.addWidget(self.dot)
        bl.addWidget(self.status, 1)
        bl.addWidget(self.button("View activity", self.show_activity))
        outer.addWidget(bar)

    def refresh(self, preferred=None):
        preferred = preferred or self.slug
        self.installed = games(self.paths)
        self.library.blockSignals(True)
        self.library.clear()
        chosen = 0
        for i, game in enumerate(self.installed):
            item = QtWidgets.QListWidgetItem(
                game.name + (f"\n{game.version}" if game.version else "")
            )
            item.setData(QtCore.Qt.UserRole, game.slug)
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
        self.desc.setText(
            game.description
            or "Catalog details are not downloaded yet. Use Refresh metadata to fetch them."
        )
        art = game.artwork.get("hero") or game.artwork.get("grid")
        pix = QtGui.QPixmap(art) if art and Path(art).is_file() else QtGui.QPixmap()
        if pix.isNull():
            self.hero.setPixmap(QtGui.QPixmap())
            self.hero.setText("RIFTLIFT")
        else:
            self.hero.setText("")
            self.hero.setPixmap(
                pix.scaled(
                    700,
                    230,
                    QtCore.Qt.KeepAspectRatioByExpanding,
                    QtCore.Qt.SmoothTransformation,
                )
            )

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
        l.addWidget(self.label("META STORE URL OR APP ID", "eye"))
        entry = QtWidgets.QLineEdit()
        entry.setPlaceholderText("https://www.meta.com/experiences/…")
        l.addWidget(entry)
        opts = QtWidgets.QHBoxLayout()
        opts.addWidget(self.label("Parallel downloads"))
        jobs = QtWidgets.QSpinBox()
        jobs.setRange(1, 32)
        jobs.setValue(8)
        opts.addWidget(jobs)
        opts.addStretch()
        steam = QtWidgets.QCheckBox("Add to Steam when finished")
        steam.setChecked(True)
        opts.addWidget(steam)
        l.addLayout(opts)
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
            count, sync = jobs.value(), steam.isChecked()
            d.accept()

            def operation():
                game = add(self.paths, value, jobs=count)
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
        self.dot.setStyleSheet("color:#9178ff")
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
            self.dot.setStyleSheet("color:#ff6b81")
            self._append_log(f"\nError: {error}\n")
            QtWidgets.QMessageBox.critical(self, "RiftLift", str(error))
        else:
            self.status.setText(message)
            self.dot.setStyleSheet("color:#43d9a3")
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
