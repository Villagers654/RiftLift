from __future__ import annotations

import threading

from PySide6 import QtCore, QtWidgets

from .config import Game, Paths, games
from .i18n import namespace
from .steam_oculus import steam_oculus_games
from .theme import STYLE
from .titlebar import wrap_dialog

STEAM_GAMES = namespace("steam_games")
ACTION = namespace("action")


class SteamScanEvents(QtCore.QObject):
    complete = QtCore.Signal(object, object)


class SteamGamesDialog(QtWidgets.QDialog):
    def __init__(self, paths: Paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.discovered: list[Game] = []
        self.existing_keys: set[str] = set()
        self.selected_game: Game | None = None
        self.events = SteamScanEvents(self)
        self.events.complete.connect(self.finish_scan)
        self.setWindowTitle(STEAM_GAMES("title"))
        self.setMinimumSize(640, 500)
        self.setStyleSheet(STYLE)

        layout = wrap_dialog(self, STEAM_GAMES("title"))
        layout.setSpacing(12)
        title = QtWidgets.QLabel(STEAM_GAMES("heading"))
        title.setObjectName("game")
        layout.addWidget(title)
        explanation = QtWidgets.QLabel(STEAM_GAMES("explanation"))
        explanation.setObjectName("muted")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.list = QtWidgets.QListWidget()
        self.list.setAccessibleName(STEAM_GAMES("accessible_name"))
        self.list.itemSelectionChanged.connect(self.select_game)
        layout.addWidget(self.list, 1)

        self.status = QtWidgets.QLabel(STEAM_GAMES("scanning"))
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QtWidgets.QHBoxLayout()
        self.scan_button = QtWidgets.QPushButton(STEAM_GAMES("scan_again"))
        self.scan_button.clicked.connect(self.scan)
        buttons.addWidget(self.scan_button)
        buttons.addStretch()
        cancel = QtWidgets.QPushButton(ACTION("cancel"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.add_button = QtWidgets.QPushButton(STEAM_GAMES("add_to_riftlift"))
        self.add_button.setObjectName("primary")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self.accept_selected)
        buttons.addWidget(self.add_button)
        layout.addLayout(buttons)

        QtCore.QTimer.singleShot(0, self.scan)

    def scan(self):
        self.discovered = []
        self.selected_game = None
        self.list.clear()
        self.list.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.add_button.setEnabled(False)
        self.status.setText(STEAM_GAMES("scanning"))

        def worker():
            try:
                self.events.complete.emit(steam_oculus_games(), None)
            except Exception as error:
                self.events.complete.emit([], error)

        threading.Thread(target=worker, daemon=True, name="steam-game-scan").start()

    def finish_scan(self, discovered, error):
        self.scan_button.setEnabled(True)
        self.list.setEnabled(True)
        if error is not None:
            self.status.setText(str(error))
            return
        self.discovered = sorted(discovered, key=lambda game: game.name.casefold())
        self.existing_keys = {game.app_key for game in games(self.paths)}
        for game in self.discovered:
            suffix = (
                STEAM_GAMES("already_in_riftlift")
                if game.app_key in self.existing_keys
                else ""
            )
            item = QtWidgets.QListWidgetItem(f"{game.name}{suffix}")
            item.setData(QtCore.Qt.UserRole, game.app_id)
            self.list.addItem(item)
        if not self.discovered:
            self.status.setText(STEAM_GAMES("none_found"))
            return
        count = len(self.discovered)
        key = "found_one" if count == 1 else "found_other"
        self.status.setText(STEAM_GAMES(key).format(count=count))
        self.list.setCurrentRow(0)

    def select_game(self):
        selected = self.list.selectedItems()
        row = self.list.row(selected[0]) if selected else -1
        if not 0 <= row < len(self.discovered):
            self.selected_game = None
            self.add_button.setEnabled(False)
            return
        self.selected_game = self.discovered[row]
        self.add_button.setText(
            STEAM_GAMES("refresh_in_riftlift")
            if self.selected_game.app_key in self.existing_keys
            else STEAM_GAMES("add_to_riftlift")
        )
        self.add_button.setEnabled(True)

    def accept_selected(self):
        if self.selected_game is not None:
            self.accept()
