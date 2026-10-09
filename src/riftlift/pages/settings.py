"""RiftLift's Settings page, embedded in the main window (not a modal)."""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6 import QtCore, QtWidgets

from ..config import (
    Paths,
    debug_logging_enabled,
    language_preference,
    set_debug_logging,
)
from ..i18n import LANGUAGES, namespace
from ..native_theme import DANGER, SUCCESS

SETTINGS = namespace("settings")
SETUP = namespace("setup")


class SettingsPage(QtWidgets.QWidget):
    def __init__(
        self,
        paths: Paths,
        on_language_change: Callable[[str], bool],
        on_run_system_check: Callable[[], None],
        on_run_setup: Callable[[], None],
        on_recheck_status: Callable[[], None],
        parent=None,
    ):
        super().__init__(parent)
        self.paths = paths
        self._on_language_change = on_language_change
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 8, 8)
        layout.setSpacing(16)
        layout.setAlignment(QtCore.Qt.AlignTop)

        layout.addWidget(self._label(SETTINGS("title"), "game"))
        layout.addSpacing(4)

        language = self._card(layout, SETTINGS("language"))
        self.language = QtWidgets.QComboBox()
        self.language.setMinimumWidth(240)
        self._codes = list(LANGUAGES)
        self._current_code = language_preference(paths)
        for code in self._codes:
            self.language.addItem(LANGUAGES[code])
        self.language.setCurrentIndex(
            self._codes.index(self._current_code)
            if self._current_code in self._codes
            else 0
        )
        language.addWidget(self.language, alignment=QtCore.Qt.AlignLeft)
        self.language.currentIndexChanged.connect(self._language_changed)

        status = self._card(layout, SETUP("status_heading"))
        status_row = QtWidgets.QHBoxLayout()
        status_row.setSpacing(10)
        self.status_icon = self._label("")
        status_row.addWidget(self.status_icon)
        self.status_text = self._label(SETUP("checking"), "description")
        self.status_text.setWordWrap(True)
        status_row.addWidget(self.status_text, 1)
        status.addLayout(status_row)
        recheck = QtWidgets.QPushButton(SETUP("recheck"))
        recheck.clicked.connect(on_recheck_status)
        status.addWidget(recheck, alignment=QtCore.Qt.AlignLeft)

        troubleshooting = self._card(layout, SETTINGS("troubleshooting"))
        system_check_explanation = self._label(
            SETTINGS(
                "windows_system_check_explanation"
                if os.name == "nt"
                else "system_check_explanation"
            ),
            "muted",
        )
        system_check_explanation.setWordWrap(True)
        troubleshooting.addWidget(system_check_explanation)
        run_check = QtWidgets.QPushButton(SETTINGS("run_system_check"))
        run_check.clicked.connect(on_run_system_check)
        troubleshooting.addWidget(run_check, alignment=QtCore.Qt.AlignLeft)
        troubleshooting.addSpacing(6)
        self.debug_logging = QtWidgets.QCheckBox(SETTINGS("debug_logging"))
        self.debug_logging.setChecked(debug_logging_enabled(paths))
        self.debug_logging.setToolTip(SETTINGS("debug_logging_tooltip"))
        if os.name == "nt":
            self.debug_logging.setToolTip(SETTINGS("windows_debug_logging_tooltip"))
        self.debug_logging.toggled.connect(
            lambda enabled: set_debug_logging(self.paths, enabled)
        )
        troubleshooting.addWidget(self.debug_logging)

        # Installed Windows builds carry the native runtime; the installer repairs it.
        if os.name != "nt":
            setup = self._card(layout, SETUP("heading"))
            setup_explanation = self._label(SETUP("explanation"), "muted")
            setup_explanation.setWordWrap(True)
            setup.addWidget(setup_explanation)
            run_setup = QtWidgets.QPushButton(SETUP("run"))
            run_setup.clicked.connect(on_run_setup)
            setup.addWidget(run_setup, alignment=QtCore.Qt.AlignLeft)

        layout.addStretch()

    def _card(self, parent: QtWidgets.QVBoxLayout, title: str) -> QtWidgets.QVBoxLayout:
        card = QtWidgets.QFrame()
        card.setObjectName("card")
        card.setMaximumWidth(720)
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(10)
        layout.addWidget(self._label(title, "section"))
        parent.addWidget(card)
        return layout

    def _label(self, text: str, name: str = "") -> QtWidgets.QLabel:
        widget = QtWidgets.QLabel(text)
        widget.setObjectName(name)
        return widget

    def set_status(self, healthy: bool, message: str) -> None:
        self.status_icon.setText("✓" if healthy else "✗")
        self.status_icon.setStyleSheet(
            f"color:{SUCCESS};font-weight:700;font-size:16px"
            if healthy
            else f"color:{DANGER};font-weight:700;font-size:16px"
        )
        self.status_text.setText(message)

    def _language_changed(self, index: int) -> None:
        code = self._codes[index]
        if code == self._current_code:
            return
        if self._on_language_change(code):
            self._current_code = code
        else:
            self.language.blockSignals(True)
            self.language.setCurrentIndex(self._codes.index(self._current_code))
            self.language.blockSignals(False)
