"""Dialogs for selecting Meta Store and local Windows games."""

from __future__ import annotations

import contextlib
import re
import shlex
import threading
from collections.abc import Callable
from dataclasses import replace
from urllib.parse import urlparse

from PySide6 import QtCore, QtGui, QtWidgets

from .config import Game, Paths
from .i18n import namespace
from .library import add, parse_download_progress
from .metadata import fetch_catalog_metadata
from .steam import sync_with_restart
from .theme import STYLE
from .titlebar import wrap_dialog
from .util import LineWriter

LINK_VALIDATION_DELAY_MS = 350

ADD_GAME = namespace("add_game")
LOCAL_GAME = namespace("local_game")
GAME = namespace("game")
ACTION = namespace("action")
LAUNCH_OPTIONS = namespace("launch_options")

_PHASE_KEYS = {
    "Preparing segments": "phase_preparing_segments",
    "Downloading": "phase_downloading",
    "Assembling files": "phase_assembling_files",
}


class LaunchOptionsDialog(QtWidgets.QDialog):
    def __init__(self, game: Game, parent=None):
        super().__init__(parent)
        self.game = game
        self.updated_game: Game | None = None
        self.setWindowTitle(LAUNCH_OPTIONS("title").format(name=game.name))
        self.setMinimumWidth(560)
        self.setStyleSheet(STYLE)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(_label(LAUNCH_OPTIONS("arguments_label"), "section"))
        self.arguments_entry = QtWidgets.QLineEdit(shlex.join(game.launch_options))
        self.arguments_entry.setPlaceholderText(LAUNCH_OPTIONS("arguments_placeholder"))
        layout.addWidget(self.arguments_entry)
        hint = _label(LAUNCH_OPTIONS("arguments_hint"), "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(_label(LAUNCH_OPTIONS("overrides_label"), "section"))
        self.overrides_entry = QtWidgets.QLineEdit(game.dll_overrides)
        self.overrides_entry.setPlaceholderText(LAUNCH_OPTIONS("overrides_placeholder"))
        layout.addWidget(self.overrides_entry)
        hint = _label(LAUNCH_OPTIONS("overrides_hint"), "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(_label(LAUNCH_OPTIONS("environment_label"), "section"))
        self.environment_entry = QtWidgets.QPlainTextEdit(
            "\n".join(f"{key}={value}" for key, value in game.environment.items())
        )
        self.environment_entry.setPlaceholderText(
            LAUNCH_OPTIONS("environment_placeholder")
        )
        self.environment_entry.setMaximumHeight(130)
        layout.addWidget(self.environment_entry)
        hint = _label(LAUNCH_OPTIONS("environment_hint"), "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.error = _label("")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        try:
            environment = {}
            for line in self.environment_entry.toPlainText().splitlines():
                if not line.strip():
                    continue
                key, separator, value = line.partition("=")
                if not separator:
                    raise ValueError(LAUNCH_OPTIONS("environment_format_error"))
                environment[key.strip()] = value
            self.updated_game = replace(
                self.game,
                launch_options=shlex.split(self.arguments_entry.text()),
                dll_overrides=self.overrides_entry.text().strip(),
                environment=environment,
            )
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.accept()


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


class _InstallEvents(QtCore.QObject):
    progress = QtCore.Signal(str, int, int)
    complete = QtCore.Signal(object, object)


class StoreGameDialog(QtWidgets.QDialog):
    def __init__(
        self,
        paths: Paths,
        open_local: Callable[[], None],
        parent=None,
        *,
        initial_url: str = "",
        simple_name: str = "",
    ):
        super().__init__(parent)
        self.paths = paths
        self.installed_game = None
        self.sync_steam = True
        self._generation = 0
        self._verified_url = ""
        self.setWindowTitle(ADD_GAME("title"))
        self.setMinimumWidth(560)
        self.setStyleSheet(STYLE)
        layout = wrap_dialog(self, ADD_GAME("title"))
        layout.setSpacing(12)
        heading = _label(ADD_GAME("heading"), "game")
        layout.addWidget(heading)
        local = QtWidgets.QPushButton(ADD_GAME("add_local"))
        local.setObjectName("link")
        local.clicked.connect(lambda: (self.reject(), open_local()))
        layout.addWidget(local, alignment=QtCore.Qt.AlignLeft)
        url_section = _label(ADD_GAME("url_section"), "section")
        layout.addWidget(url_section)
        browse = QtWidgets.QPushButton(ADD_GAME("browse_store"))
        browse.setObjectName("link")
        browse.clicked.connect(self._browse_store)
        layout.addWidget(browse, alignment=QtCore.Qt.AlignLeft)
        self.entry = QtWidgets.QLineEdit()
        self.entry.setPlaceholderText(ADD_GAME("url_placeholder"))
        layout.addWidget(self.entry)
        self.validation = _label(ADD_GAME("paste_valid_link"), "muted")
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)
        self.steam = QtWidgets.QCheckBox(ADD_GAME("add_to_steam"))
        self.steam.setChecked(True)
        layout.addWidget(self.steam)
        self.progress = QtWidgets.QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.cancel_button = buttons.button(QtWidgets.QDialogButtonBox.Cancel)
        self.cancel_button.setIcon(QtGui.QIcon())
        self.cancel_button.setText(ACTION("cancel"))
        self.submit = buttons.addButton(
            GAME("install"), QtWidgets.QDialogButtonBox.AcceptRole
        )
        self.submit.setObjectName("primary")
        self.submit.setEnabled(False)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.events = _ValidationEvents(self)
        self.events.complete.connect(self._finish_validation)
        self.install_events = _InstallEvents(self)
        self.install_events.progress.connect(self._update_progress)
        self.install_events.complete.connect(self._finish_install)
        self.timer.timeout.connect(self._check_catalog)
        self.entry.textChanged.connect(self._validate)
        self.entry.returnPressed.connect(self._accept_selection)
        self.submit.clicked.connect(self._accept_selection)

        if simple_name:
            self.setWindowTitle(simple_name)
            self.titlebar.set_title(simple_name)
            heading.setText(ADD_GAME("install_heading"))
            local.hide()
            url_section.hide()
            browse.hide()
            self.entry.hide()
            self.entry.blockSignals(True)
            self.entry.setText(initial_url)
            self.entry.blockSignals(False)
            self._verified_url = initial_url
            self.submit.setEnabled(True)
            self.validation.setText(
                ADD_GAME("confirm_install").format(name=simple_name)
            )
        elif initial_url:
            self.entry.setText(initial_url)

    def _browse_store(self) -> None:
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl("https://www.meta.com/experiences/pcvr/")
        )

    def _validate(self, value: str) -> None:
        self._generation += 1
        self._verified_url = ""
        self.timer.stop()
        self.submit.setEnabled(False)
        if not is_valid_rift_store_url(value):
            self.validation.setText(ADD_GAME("paste_valid_link"))
            return
        self.validation.setText(ADD_GAME("checking_link"))
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
                ADD_GAME("game_not_found")
                if "has no catalog metadata" in str(error)
                else ADD_GAME("link_check_failed")
            )
            return
        if not metadata or not metadata.name.strip():
            self.validation.setText(ADD_GAME("game_not_found"))
            return
        self._verified_url = value
        self.submit.setEnabled(True)
        self.validation.setText(ADD_GAME("ready_to_install").format(name=metadata.name))

    def _accept_selection(self) -> None:
        value = self.entry.text().strip()
        if value != self._verified_url:
            self.entry.setFocus()
            return
        self._start_install(value)

    def _start_install(self, url: str) -> None:
        self.sync_steam = self.steam.isChecked()
        self.entry.setEnabled(False)
        self.steam.setEnabled(False)
        self.submit.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.validation.setText(ADD_GAME("starting_install"))

        def emit_line(line: str) -> None:
            parsed = parse_download_progress(line)
            if parsed is not None:
                self.install_events.progress.emit(*parsed)
            elif line.strip():
                self.install_events.progress.emit(line.strip(), -1, -1)

        def worker() -> None:
            try:
                with contextlib.redirect_stdout(LineWriter(emit_line)):
                    game = add(self.paths, url)
                    if self.sync_steam:
                        sync_with_restart(self.paths)
                self.install_events.complete.emit(game, None)
            except Exception as error:
                self.install_events.complete.emit(None, error)

        threading.Thread(target=worker, daemon=True, name="riftlift-install").start()

    def _update_progress(self, label: str, current: int, total: int) -> None:
        if key := _PHASE_KEYS.get(label):
            label = ADD_GAME(key)
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(current)
            self.validation.setText(f"{label}: {current}/{total}")
        else:
            self.progress.setRange(0, 0)
            self.validation.setText(label)

    def _finish_install(self, game, error) -> None:
        if error is not None:
            self.progress.hide()
            self.entry.setEnabled(True)
            self.steam.setEnabled(True)
            self.submit.setEnabled(True)
            self.cancel_button.setEnabled(True)
            self.validation.setText(str(error))
            return
        self.installed_game = game
        self.accept()


class LocalGameDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.executable = ""
        self.game_name: str | None = None
        self.arguments: str | None = None
        self.artwork: str | None = None
        self.sync_steam = True
        self.setWindowTitle(LOCAL_GAME("title"))
        self.setMinimumWidth(600)
        self.setStyleSheet(STYLE)
        layout = wrap_dialog(self, LOCAL_GAME("title"))
        layout.setSpacing(12)
        layout.addWidget(_label(LOCAL_GAME("title"), "game"))
        layout.addWidget(_label(LOCAL_GAME("hint"), "muted"))
        self.executable_entry = self._file_row(
            layout,
            LOCAL_GAME("executable"),
            "/path/to/game.exe",
            "Windows games (*.exe)",
        )
        layout.addWidget(_label(LOCAL_GAME("name"), "section"))
        self.name_entry = QtWidgets.QLineEdit()
        self.name_entry.setPlaceholderText(LOCAL_GAME("name_placeholder"))
        layout.addWidget(self.name_entry)
        layout.addWidget(_label(LOCAL_GAME("arguments"), "section"))
        self.arguments_entry = QtWidgets.QLineEdit()
        layout.addWidget(self.arguments_entry)
        self.artwork_entry = self._file_row(
            layout,
            LOCAL_GAME("artwork"),
            "PNG, JPEG, or WebP",
            "Images (*.png *.jpg *.jpeg *.webp)",
        )
        self.steam = QtWidgets.QCheckBox(ADD_GAME("add_to_steam"))
        self.steam.setChecked(True)
        layout.addWidget(self.steam)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        cancel_button = buttons.button(QtWidgets.QDialogButtonBox.Cancel)
        cancel_button.setIcon(QtGui.QIcon())
        cancel_button.setText(ACTION("cancel"))
        self.submit = buttons.addButton(
            LOCAL_GAME("add"), QtWidgets.QDialogButtonBox.AcceptRole
        )
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
        browse = QtWidgets.QPushButton(LOCAL_GAME("browse"))
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
