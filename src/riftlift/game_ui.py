"""Dialogs for selecting Meta Store and local Windows games."""

from __future__ import annotations

import contextlib
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from urllib.parse import urlparse

from meta_pcvr_downloader.api import Build
from PySide6 import QtCore, QtGui, QtWidgets

from .auth import accounts
from .builds import build_label, default_build
from .config import Game, Paths
from .desktop_services import supports_steam_shortcuts
from .download_job import DownloadJob
from .i18n import namespace
from .library import (
    ALL_BUILDS,
    available_builds,
    join_launch_arguments,
    split_launch_arguments,
)
from .metadata import fetch_catalog_metadata
from .theme import STYLE
from .titlebar import wrap_dialog
from .util import RiftLiftError

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
        self.arguments_entry = QtWidgets.QLineEdit(
            join_launch_arguments(game.launch_options)
        )
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
                launch_options=split_launch_arguments(self.arguments_entry.text()),
                dll_overrides=self.overrides_entry.text().strip(),
                environment=environment,
            )
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.accept()


_RIFT_STORE_PATHS = {
    # meta.com/[locale/]experiences/pcvr/[slug/]<id>/
    "meta.com": re.compile(
        r"/(?:[a-z]{2}-[a-z]{2}/)?experiences/pcvr/(?:[^/]+/)?(?P<app_id>\d{8,})/?",
        re.IGNORECASE,
    ),
    # Legacy oculus.com/experiences/rift/[slug/]<id>/ links.
    "oculus.com": re.compile(
        r"/(?:[a-z]{2}-[a-z]{2}/)?experiences/rift/(?:[^/]+/)?(?P<app_id>\d{8,})/?",
        re.IGNORECASE,
    ),
}


def rift_store_app_id(value: str) -> str | None:
    """Return the app ID from an exact Meta Rift/PCVR product URL."""
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    pattern = _RIFT_STORE_PATHS.get(host.removeprefix("www."))
    match = pattern.fullmatch(parsed.path) if pattern else None
    if not (
        parsed.scheme.lower() == "https"
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and match
    ):
        return None
    return match.group("app_id")


def is_valid_rift_store_url(value: str) -> bool:
    return rift_store_app_id(value) is not None


def _themed_dialog(parent, title: str, text: str):
    # Same construction as main_window's themed dialogs.
    dialog = QtWidgets.QDialog(parent)
    dialog.setStyleSheet(STYLE)
    dialog.setMinimumWidth(420)
    layout = wrap_dialog(dialog, title, margins=(24, 22, 24, 22))
    layout.setSpacing(14)
    label = QtWidgets.QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(QtCore.Qt.PlainText)
    layout.addWidget(label)
    # A top-level dialog doesn't grow for wrapped text; reserve the height the
    # text needs, in the themed font, at the narrowest width the dialog can have.
    label.ensurePolished()
    left, _top, right, _bottom = layout.getContentsMargins()
    label.setMinimumHeight(label.heightForWidth(dialog.minimumWidth() - left - right))
    return dialog, layout


def _themed_question(parent, title: str, text: str) -> bool:
    dialog, layout = _themed_dialog(parent, title, text)
    buttons = QtWidgets.QHBoxLayout()
    buttons.addStretch()
    no_button = QtWidgets.QPushButton(ACTION("no"))
    no_button.clicked.connect(dialog.reject)
    buttons.addWidget(no_button)
    yes_button = QtWidgets.QPushButton(ACTION("yes"))
    yes_button.setObjectName("primary")
    yes_button.clicked.connect(dialog.accept)
    buttons.addWidget(yes_button)
    layout.addLayout(buttons)
    return dialog.exec() == QtWidgets.QDialog.Accepted


def _themed_notice_dialog(parent, title: str, text: str, details: str = ""):
    dialog, layout = _themed_dialog(parent, title, text)
    if details:
        # A long list scrolls in its own box instead of growing the dialog
        # past the screen; it can be selected and copied, too.
        dialog.setMinimumWidth(560)
        view = QtWidgets.QPlainTextEdit(details)
        view.setReadOnly(True)
        line = view.fontMetrics().lineSpacing()
        view.setMinimumHeight(4 * line)
        view.setMaximumHeight(12 * line)
        layout.addWidget(view)
    ok_button = QtWidgets.QPushButton(ACTION("ok"))
    ok_button.setObjectName("primary")
    ok_button.clicked.connect(dialog.accept)
    layout.addWidget(ok_button, alignment=QtCore.Qt.AlignRight)
    return dialog


def _themed_notice(parent, title: str, text: str, details: str = "") -> None:
    _themed_notice_dialog(parent, title, text, details).exec()


def _label(text: str, name: str = "") -> QtWidgets.QLabel:
    widget = QtWidgets.QLabel(text)
    widget.setObjectName(name)
    return widget


def _is_signed_in(paths: Paths) -> bool:
    return bool(accounts(paths))


@dataclass(frozen=True)
class LinkCheck:
    """Outcome of checking a store link: a display name and downloadable builds."""

    name: str
    builds: list[Build] = field(default_factory=list)


def check_store_link(paths: Paths, app_id: str) -> LinkCheck:
    """Verify an app through the account's builds, then the public store page.

    Delisted games such as Echo VR have no catalog data on their store page,
    but owners can still download them, so the signed-in build list wins.
    """
    signed_in = _is_signed_in(paths)
    expired = False
    if signed_in:
        try:
            builds = available_builds(paths, app_id)
        except Exception as error:  # fall back to the public store page
            print(f"warning: could not list builds for {app_id}: {error}")
            # Meta answers a stale or revoked token with an OAuthException.
            expired = any(
                text in str(error)
                for text in ("OAuthException", "HTTP 401", "HTTP 403")
            )
        else:
            return LinkCheck(builds[0].app_name, builds)
    try:
        metadata = fetch_catalog_metadata(app_id)
    except RiftLiftError as error:
        if "has no catalog metadata" in str(error):
            if expired:
                raise RiftLiftError(ADD_GAME("sign_in_expired")) from error
            if not signed_in:
                raise RiftLiftError(ADD_GAME("sign_in_to_verify")) from error
        raise
    if not metadata.name.strip():
        raise RiftLiftError(
            f"Meta's store page has no catalog metadata for app {app_id}"
        )
    return LinkCheck(metadata.name)


# Install failures the dialog explains; anything else reads as a failed download.
_INSTALL_ERRORS = frozenset(
    {"sign_in_required", "download_failed", "worker_failed", "not_launchable"}
)


def _deliver(signal, *args) -> None:
    """Hand a worker thread's result to the dialog, unless it is gone.

    Quitting deletes the dialog's event objects while a link check or version
    listing may still be running; its result then has nowhere to go.
    """
    with contextlib.suppress(RuntimeError):  # "Signal source has been deleted"
        signal.emit(*args)


class _ValidationEvents(QtCore.QObject):
    complete = QtCore.Signal(int, str, object, object)


class _InstallEvents(QtCore.QObject):
    progress = QtCore.Signal(str, int, int)
    version = QtCore.Signal(str)
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
        self.install_warning = None
        self._job = None
        self._busy = False
        self._close_when_paused = False
        self._paused = False
        self._generation = 0
        self._verified_url = ""
        self._game_name = simple_name
        self._builds: list[Build] = []
        self._version_prefix = ""
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
        self.version_section = _label(ADD_GAME("version_section"), "section")
        self.version_section.hide()
        layout.addWidget(self.version_section)
        self.versions = QtWidgets.QComboBox()
        self.versions.setObjectName("versions")
        self.versions.hide()
        layout.addWidget(self.versions)
        self.steam = QtWidgets.QCheckBox(ADD_GAME("add_to_steam"))
        self.steam.setChecked(supports_steam_shortcuts())
        self.steam.setEnabled(supports_steam_shortcuts())
        # Shown only where Steam shortcuts work; it stays unchecked elsewhere.
        self.steam.setVisible(supports_steam_shortcuts())
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
        buttons.rejected.connect(self._cancel_install)
        layout.addWidget(buttons)

        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.events = _ValidationEvents(self)
        self.events.complete.connect(self._finish_validation)
        self.install_events = _InstallEvents(self)
        self.install_events.progress.connect(self._update_progress)
        self.install_events.version.connect(self._set_version_prefix)
        self.install_events.complete.connect(self._finish_install)
        self.timer.timeout.connect(self._check_catalog)
        self.entry.textChanged.connect(self._validate)
        self.entry.returnPressed.connect(self._accept_selection)
        self.submit.clicked.connect(self._accept_selection)

        if simple_name:
            self.setWindowTitle(simple_name)
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
            self._load_versions(initial_url)
        elif initial_url:
            self.entry.setText(initial_url)

    def _browse_store(self) -> None:
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl("https://www.meta.com/experiences/pcvr/")
        )

    def _set_builds(self, builds: list[Build]) -> None:
        """Fill the version picker. The first entry installs the newest build."""
        self._builds = list(builds)
        self.versions.clear()
        if not builds:
            self.version_section.hide()
            self.versions.hide()
            return
        # The first entry is what a plain install downloads: the newest build
        # Meta offers, even when an alpha or beta build is newer.
        default = default_build(builds)
        ordered = [default, *(build for build in builds if build is not default)]
        for index, build in enumerate(ordered):
            label = build_label(build)
            channels = [
                channel
                for channel in getattr(build, "channels", ())
                if channel.upper() != "LIVE"
            ]
            if channels:
                label = f"{label} · {', '.join(channels)}"
            if index == 0:
                self.versions.addItem(
                    ADD_GAME("latest_version").format(label=label), None
                )
            else:
                self.versions.addItem(label, build.binary_id)
        if len(builds) > 1:
            self.versions.addItem(
                ADD_GAME("all_versions").format(count=len(builds)), ALL_BUILDS
            )
        self.version_section.show()
        self.versions.show()

    def _load_versions(self, url: str) -> None:
        """Fetch the build list for an already confirmed owned game."""
        app_id = rift_store_app_id(url)
        if app_id is None or not _is_signed_in(self.paths):
            return
        token = self._generation
        self.version_section.setText(ADD_GAME("loading_versions"))
        self.version_section.show()

        def worker() -> None:
            try:
                result = LinkCheck(
                    self._game_name, available_builds(self.paths, app_id)
                )
                _deliver(self.events.complete, token, url, result, None)
            except Exception as error:
                _deliver(self.events.complete, token, url, None, error)

        threading.Thread(
            target=worker, daemon=True, name="riftlift-list-versions"
        ).start()

    def _validate(self, value: str) -> None:
        self._generation += 1
        self._verified_url = ""
        self._set_builds([])
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
                result = check_store_link(self.paths, app_id)
                _deliver(self.events.complete, token, value, result, None)
            except Exception as error:
                _deliver(self.events.complete, token, value, None, error)

        threading.Thread(
            target=worker, daemon=True, name="riftlift-link-validation"
        ).start()

    def _finish_validation(self, token: int, value: str, result, error) -> None:
        if token != self._generation or value != self.entry.text().strip():
            return
        if self._verified_url == value:
            # Version list for a game that was already confirmed (owned library).
            self.version_section.setText(ADD_GAME("version_section"))
            if result is not None and self.progress.isHidden():
                self._set_builds(result.builds)
            else:
                self.version_section.hide()
            return
        if error is not None:
            message = str(error)
            if message == ADD_GAME("sign_in_to_verify"):
                self.validation.setText(message)
            elif "has no catalog metadata" in message:
                self.validation.setText(ADD_GAME("game_not_found"))
            else:
                self.validation.setText(ADD_GAME("link_check_failed"))
            return
        if not result or not result.name.strip():
            self.validation.setText(ADD_GAME("game_not_found"))
            return
        self._verified_url = value
        self._game_name = result.name
        self._set_builds(result.builds)
        self.submit.setEnabled(True)
        self.validation.setText(ADD_GAME("ready_to_install").format(name=result.name))

    def _accept_selection(self) -> None:
        value = self.entry.text().strip()
        if value != self._verified_url:
            self.entry.setFocus()
            return
        selector = self.versions.currentData() if self.versions.count() else None
        # Resuming continues the run the question already allowed; the link and
        # the version can't change while paused.
        if (
            selector == ALL_BUILDS
            and not self._paused
            and not _themed_question(
                self,
                ADD_GAME("all_versions").format(count=len(self._builds)),
                ADD_GAME("all_versions_confirm").format(
                    count=len(self._builds), name=self._game_name
                ),
            )
        ):
            return
        self._start_install(value, selector)

    def _start_install(self, url: str, selector: str | None = None) -> None:
        if self._busy:
            return
        if self._job is not None:
            self._job.deleteLater()
        self._close_when_paused = False
        self._paused = False
        self._version_prefix = ""
        self.sync_steam = self.steam.isChecked()
        self.entry.setEnabled(False)
        self.steam.setEnabled(False)
        self.versions.setEnabled(False)
        self.submit.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.validation.setText(ADD_GAME("starting_install"))

        self._busy = True
        self._job = DownloadJob(
            self.paths, url, self.sync_steam, self, build_selector=selector
        )
        self._job.progress.connect(self.install_events.progress)
        self._job.version.connect(self._start_version)
        self._job.complete.connect(self.install_events.complete)
        self._job.paused.connect(self._finish_pause)
        self._job.finishing.connect(self._finish_download)
        self.cancel_button.setText(ADD_GAME("pause_download"))
        self.cancel_button.setAccessibleName(self.cancel_button.text())
        self.cancel_button.setEnabled(True)
        self.cancel_button.setFocus()
        self._job.start()

    def _finish_download(self):
        self.cancel_button.setEnabled(False)
        self.validation.setText(ADD_GAME("finishing_install"))

    def _start_version(self, index: int, total: int) -> None:
        # With every version selected, Pause works again between versions.
        self.install_events.version.emit(
            ADD_GAME("installing_version").format(index=index, total=total)
        )
        self.cancel_button.setEnabled(True)

    def _cancel_install(self):
        if not self._busy:
            super().reject()
            return
        if self.cancel_button.isEnabled():
            self.cancel_button.setEnabled(False)
            self.validation.setText(ADD_GAME("pausing_download"))
            self._job.pause()

    def _finish_pause(self):
        self._busy = False
        self._paused = True
        self.progress.hide()
        self.submit.setText(ADD_GAME("resume_download"))
        self.submit.setAccessibleName(self.submit.text())
        self.submit.setEnabled(True)
        self.cancel_button.setText(ACTION("cancel"))
        self.cancel_button.setAccessibleName(self.cancel_button.text())
        self.cancel_button.setEnabled(True)
        self.validation.setText(ADD_GAME("download_paused"))
        self.submit.setFocus()
        if self._close_when_paused:
            super().reject()

    def reject(self):
        if self._busy:
            self._close_when_paused = True
            self._cancel_install()
            return
        super().reject()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)

    def _set_version_prefix(self, prefix: str) -> None:
        self._version_prefix = prefix

    def _update_progress(self, label: str, current: int, total: int) -> None:
        assembling = label == "Assembling files"
        if key := _PHASE_KEYS.get(label):
            label = ADD_GAME(key)
        if self._version_prefix:
            label = f"{self._version_prefix} · {label}"
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(current)
            self.validation.setText(
                ADD_GAME("assembling_progress").format(
                    label=label, done=current / 1024, total=total / 1024
                )
                if assembling
                else f"{label}: {current}/{total}"
            )
        else:
            self.progress.setRange(0, 0)
            self.validation.setText(label)

    def _log(self, text: str) -> None:
        """Add text to the library window's Activity log, if there is one."""
        log = getattr(self.parent(), "_append_log", None)
        if text and log is not None:
            log(text if text.endswith("\n") else text + "\n")

    def _finish_install(self, game, error) -> None:
        self._busy = False
        failed = self._job.failed_versions if self._job is not None else []
        # List the failed versions even when none installed: the plain
        # failure text below cannot say which ones failed or why.
        if failed:
            reasons = self._job.failed_version_reasons
            self._log("".join(f"\n{label}: {why}" for label, why in reasons.items()))
            # The library window offers no way to open its Activity log, so the
            # notice says why; versions that failed the same way share a line.
            groups: dict[tuple[str, str], list[str]] = {}
            for label, kind in failed:
                groups.setdefault((kind, reasons.get(label, "")), []).append(label)
            details = "\n".join(
                f"{', '.join(labels)}: {ADD_GAME('version_' + kind)}"
                + (f" ({why})" if why else "")
                for (kind, why), labels in groups.items()
            )
            _themed_notice(
                self,
                self._game_name or ADD_GAME("title"),
                ADD_GAME("versions_failed").format(
                    failed=len(failed), total=len(self._builds)
                ),
                details,
            )
        if game is not None:
            self.installed_game = game
            self.install_warning = error
            self.accept()
            return
        if error is not None:
            self.progress.hide()
            self.entry.setEnabled(True)
            self.steam.setEnabled(supports_steam_shortcuts())
            self.versions.setEnabled(True)
            self.submit.setEnabled(True)
            self.cancel_button.setEnabled(True)
            self.cancel_button.setText(ACTION("cancel"))
            self.cancel_button.setAccessibleName(self.cancel_button.text())
            self.submit.setText(ADD_GAME("retry_download"))
            self.submit.setAccessibleName(self.submit.text())
            key = (
                error
                if isinstance(error, str) and error in _INSTALL_ERRORS
                else "download_failed"
            )
            message = ADD_GAME(key)
            detail = self._job.error_detail if self._job is not None else ""
            if detail:
                message += "\n" + detail
                self._log(f"\nDownload failed: {detail}\n")
            self.validation.setText(message)
            self.submit.setFocus()
            if self._close_when_paused:
                super().reject()
            return


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
        self.steam.setChecked(supports_steam_shortcuts())
        self.steam.setEnabled(supports_steam_shortcuts())
        # Shown only where Steam shortcuts work; it stays unchecked elsewhere.
        self.steam.setVisible(supports_steam_shortcuts())
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
