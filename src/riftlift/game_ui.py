"""Dialogs for selecting Meta Store and local Windows games."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from urllib.parse import urlparse

from PySide6 import QtCore, QtGui, QtWidgets

from .metadata import fetch_catalog_metadata
from .theme import STYLE

LINK_VALIDATION_DELAY_MS = 350


def rift_store_app_id(value: str) -> str | None:
    """Return the app ID from an exact Meta Rift/PCVR product URL."""
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    match = re.fullmatch(
        r"/(?:[a-z]{2}-[a-z]{2}/)?experiences/pcvr/[^/]+/(?P<app_id>\d{8,})/?",
        parsed.path,
        re.IGNORECASE,
    )
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
    return rift_store_app_id(value) is not None


def _label(text: str, name: str = "") -> QtWidgets.QLabel:
    widget = QtWidgets.QLabel(text)
    widget.setObjectName(name)
    return widget


class _ValidationEvents(QtCore.QObject):
    complete = QtCore.Signal(int, str, object, object)


class StoreGameDialog(QtWidgets.QDialog):
    def __init__(self, open_local: Callable[[], None], parent=None):
        super().__init__(parent)
        self.url = ""
        self.sync_steam = True
        self._generation = 0
        self._verified_url = ""
        self.setWindowTitle("Add a Rift game")
        self.setMinimumWidth(560)
        self.setStyleSheet(STYLE)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(12)
        layout.addWidget(_label("Add to your library", "game"))
        local = QtWidgets.QPushButton("Add a local game…")
        local.setObjectName("link")
        local.clicked.connect(lambda: (self.reject(), open_local()))
        layout.addWidget(local, alignment=QtCore.Qt.AlignLeft)
        layout.addWidget(_label("Meta Rift store URL", "section"))
        self.entry = QtWidgets.QLineEdit()
        self.entry.setPlaceholderText("https://www.meta.com/experiences/pcvr/…")
        layout.addWidget(self.entry)
        self.validation = _label(
            "Paste a valid Meta Rift store link to continue.", "muted"
        )
        layout.addWidget(self.validation)
        self.steam = QtWidgets.QCheckBox("Add to Steam when finished")
        self.steam.setChecked(True)
        layout.addWidget(self.steam)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setIcon(QtGui.QIcon())
        self.submit = buttons.addButton(
            "Install", QtWidgets.QDialogButtonBox.AcceptRole
        )
        self.submit.setObjectName("primary")
        self.submit.setEnabled(False)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.events = _ValidationEvents(self)
        self.events.complete.connect(self._finish_validation)
        self.timer.timeout.connect(self._check_catalog)
        self.entry.textChanged.connect(self._validate)
        self.entry.returnPressed.connect(self._accept_selection)
        self.submit.clicked.connect(self._accept_selection)

    def _validate(self, value: str) -> None:
        self._generation += 1
        self._verified_url = ""
        self.timer.stop()
        self.submit.setEnabled(False)
        if not is_valid_rift_store_url(value):
            self.validation.setText("Paste a valid Meta Rift store link to continue.")
            return
        self.validation.setText("Checking Rift store link…")
        self.timer.start(LINK_VALIDATION_DELAY_MS)

    def _check_catalog(self) -> None:
        token = self._generation
        value = self.entry.text().strip()
        app_id = rift_store_app_id(value)
        if app_id is None:
            return

        def worker() -> None:
            try:
                metadata = fetch_catalog_metadata(app_id)
                self.events.complete.emit(token, value, metadata, None)
            except Exception as error:
                self.events.complete.emit(token, value, None, error)

        threading.Thread(
            target=worker, daemon=True, name="riftlift-link-validation"
        ).start()

    def _finish_validation(self, token: int, value: str, metadata, error) -> None:
        if token != self._generation or value != self.entry.text().strip():
            return
        if error is not None:
            self.validation.setText(
                "This Rift store game could not be found."
                if "has no catalog metadata" in str(error)
                else "Could not verify this link. Check your connection."
            )
            return
        if not metadata or not metadata.name.strip():
            self.validation.setText("This Rift store game could not be found.")
            return
        self._verified_url = value
        self.submit.setEnabled(True)
        self.validation.setText(f"Ready to install {metadata.name}.")

    def _accept_selection(self) -> None:
        value = self.entry.text().strip()
        if value != self._verified_url:
            self.entry.setFocus()
            return
        self.url = value
        self.sync_steam = self.steam.isChecked()
        self.accept()


class LocalGameDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.executable = ""
        self.game_name: str | None = None
        self.arguments: str | None = None
        self.artwork: str | None = None
        self.sync_steam = True
        self.setWindowTitle("Add a local VR game")
        self.setMinimumWidth(600)
        self.setStyleSheet(STYLE)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(12)
        layout.addWidget(_label("Add a local VR game", "game"))
        layout.addWidget(
            _label(
                "Choose an installed Windows VR game. RiftLift leaves its files in place.",
                "muted",
            )
        )
        self.executable_entry = self._file_row(
            layout, "Game executable", "/path/to/game.exe", "Windows games (*.exe)"
        )
        layout.addWidget(_label("Name", "section"))
        self.name_entry = QtWidgets.QLineEdit()
        self.name_entry.setPlaceholderText("Filled from the executable")
        layout.addWidget(self.name_entry)
        layout.addWidget(_label("Launch arguments (optional)", "section"))
        self.arguments_entry = QtWidgets.QLineEdit()
        layout.addWidget(self.arguments_entry)
        self.artwork_entry = self._file_row(
            layout,
            "Cover image (optional)",
            "PNG, JPEG, or WebP",
            "Images (*.png *.jpg *.jpeg *.webp)",
        )
        self.steam = QtWidgets.QCheckBox("Add to Steam when finished")
        self.steam.setChecked(True)
        layout.addWidget(self.steam)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setIcon(QtGui.QIcon())
        self.submit = buttons.addButton("Add", QtWidgets.QDialogButtonBox.AcceptRole)
        self.submit.setObjectName("primary")
        self.submit.setEnabled(False)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.executable_entry.textChanged.connect(self._executable_changed)
        self.submit.clicked.connect(self._accept_selection)

    def _file_row(
        self,
        layout: QtWidgets.QVBoxLayout,
        heading: str,
        placeholder: str,
        file_filter: str,
    ) -> QtWidgets.QLineEdit:
        layout.addWidget(_label(heading, "section"))
        row = QtWidgets.QHBoxLayout()
        entry = QtWidgets.QLineEdit()
        entry.setPlaceholderText(placeholder)
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(lambda: self._choose_file(entry, file_filter))
        row.addWidget(entry, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        return entry

    def _choose_file(self, entry: QtWidgets.QLineEdit, file_filter: str) -> None:
        selected, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose a file", entry.text(), file_filter
        )
        if selected:
            entry.setText(selected)

    def _executable_changed(self, value: str) -> None:
        path = QtCore.QFileInfo(value.strip())
        self.submit.setEnabled(path.isFile() and path.suffix().casefold() == "exe")
        if path.isFile() and not self.name_entry.text().strip():
            self.name_entry.setText(path.completeBaseName())

    def _accept_selection(self) -> None:
        if not self.submit.isEnabled():
            self.executable_entry.setFocus()
            return
        self.executable = self.executable_entry.text().strip()
        self.game_name = self.name_entry.text().strip() or None
        self.arguments = self.arguments_entry.text().strip() or None
        self.artwork = self.artwork_entry.text().strip() or None
        self.sync_steam = self.steam.isChecked()
        self.accept()
