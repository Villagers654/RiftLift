"""Account UI for RiftLift's browser-backed Meta authentication."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .auth import (
    available_browsers,
    complete_browser_login,
    is_signed_in,
    launch_browser_login,
    sign_out,
)
from .config import Paths
from .theme import STYLE


class AuthDialog(QtWidgets.QDialog):
    """RiftLift-owned shell around Meta's hosted browser authentication."""

    def __init__(self, paths: Paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.browser = ""
        self.process = None
        self.completed = False
        self.setWindowTitle("Meta account")
        self.setMinimumWidth(520)
        self.setStyleSheet(STYLE)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QtWidgets.QLabel("Sign in to Meta")
        title.setObjectName("game")
        layout.addWidget(title)
        explanation = QtWidgets.QLabel(
            "Choose a browser. RiftLift opens a dedicated sign-in window and "
            "returns here automatically when Meta finishes. Your password and "
            "security codes go only to Meta."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.status = QtWidgets.QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.browser_buttons: dict[str, QtWidgets.QPushButton] = {}
        installed = set(available_browsers())
        for browser, name in (("edge", "Microsoft Edge"), ("firefox", "Firefox")):
            button = QtWidgets.QPushButton(f"Continue with {name}")
            button.setObjectName("primary" if browser == "edge" else "")
            button.setEnabled(browser in installed)
            if browser not in installed:
                button.setText(f"{name} is not installed")
            button.clicked.connect(
                lambda _checked=False, value=browser: self.start(value)
            )
            layout.addWidget(button)
            self.browser_buttons[browser] = button

        row = QtWidgets.QHBoxLayout()
        self.reset = QtWidgets.QPushButton("Sign out and reset")
        self.reset.clicked.connect(self.reset_login)
        row.addWidget(self.reset)
        row.addStretch()
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(900)
        self.timer.timeout.connect(self.check_login)
        self.show_state()

    def show_state(self):
        signed_in = is_signed_in(self.paths)
        self.status.setText(
            "RiftLift is signed in to Meta."
            if signed_in
            else "RiftLift is signed out. Choose Edge or Firefox to continue."
        )
        self.reset.setVisible(signed_in)

    def set_browser_buttons_enabled(self, enabled: bool):
        installed = set(available_browsers())
        for browser, button in self.browser_buttons.items():
            button.setEnabled(enabled and browser in installed)

    def start(self, browser: str):
        self.stop_browser()
        sign_out(self.paths)
        try:
            self.process = launch_browser_login(self.paths, browser)
        except Exception as error:
            self.status.setText(str(error))
            self.set_browser_buttons_enabled(True)
            return
        self.browser = browser
        self.status.setText(
            f"Waiting for Meta in {'Microsoft Edge' if browser == 'edge' else 'Firefox'}…"
        )
        self.set_browser_buttons_enabled(False)
        self.reset.setText("Cancel and start over")
        self.reset.setVisible(True)
        self.timer.start()

    def check_login(self):
        try:
            complete_browser_login(self.paths, self.browser)
        except Exception:
            if self.process is not None and self.process.poll() is not None:
                self.timer.stop()
                self.process = None
                self.status.setText(
                    "The browser closed before sign-in finished. Choose a browser to retry."
                )
                self.set_browser_buttons_enabled(True)
                self.reset.setText("Sign out and reset")
                self.reset.setVisible(False)
            return
        self.timer.stop()
        self.completed = True
        self.status.setText("Signed in. Returning to RiftLift…")
        self.stop_browser()
        QtCore.QTimer.singleShot(500, self.accept)

    def stop_browser(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        self.process = None

    def reset_login(self):
        self.timer.stop()
        self.stop_browser()
        sign_out(self.paths)
        self.browser = ""
        self.set_browser_buttons_enabled(True)
        self.reset.setText("Sign out and reset")
        self.reset.setVisible(False)
        self.status.setText("Signed out. Choose a browser to start a fresh sign-in.")

    def reject(self):
        self.timer.stop()
        self.stop_browser()
        super().reject()
