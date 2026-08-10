from __future__ import annotations

import threading

from PySide6 import QtCore, QtWidgets

from .config import Game, Paths, games
from .steam_oculus import steam_oculus_games
from .theme import STYLE


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
        self.setWindowTitle("Steam games with Oculus mode")
        self.setMinimumSize(640, 500)
        self.setStyleSheet(STYLE)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(12)
        title = QtWidgets.QLabel("Add an installed Steam VR game")
        title.setObjectName("game")
        layout.addWidget(title)
        explanation = QtWidgets.QLabel(
            "RiftLift scans your installed Steam games for a compatible Oculus "
            "mode. Adding one does not download or duplicate the game."
        )
        explanation.setObjectName("muted")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.list = QtWidgets.QListWidget()
        self.list.setAccessibleName("Compatible Steam games")
        self.list.itemSelectionChanged.connect(self.select_game)
        layout.addWidget(self.list, 1)

        self.status = QtWidgets.QLabel("Scanning installed Steam games…")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QtWidgets.QHBoxLayout()
        self.scan_button = QtWidgets.QPushButton("Scan again")
        self.scan_button.clicked.connect(self.scan)
        buttons.addWidget(self.scan_button)
        buttons.addStretch()
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.add_button = QtWidgets.QPushButton("Add to RiftLift")
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
        self.status.setText("Scanning installed Steam games…")

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
                " (already in RiftLift)" if game.app_key in self.existing_keys else ""
            )
            item = QtWidgets.QListWidgetItem(f"{game.name}{suffix}")
            item.setData(QtCore.Qt.UserRole, game.app_id)
            self.list.addItem(item)
        if not self.discovered:
            self.status.setText(
                "No compatible games were found. Install a Steam game with an "
                "Oculus mode, then choose Scan again."
            )
            return
        count = len(self.discovered)
        self.status.setText(
            f"Found {count} compatible Steam game{'s' if count != 1 else ''}. "
            "Select one to add it to your RiftLift library."
        )
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
            "Refresh in RiftLift"
            if self.selected_game.app_key in self.existing_keys
            else "Add to RiftLift"
        )
        self.add_button.setEnabled(True)

    def accept_selected(self):
        if self.selected_game is not None:
            self.accept()
