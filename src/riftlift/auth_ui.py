"""Account UI for RiftLift's browser-backed Meta authentication."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from .auth import complete_browser_login, is_signed_in, sign_out
from .auth_browser import default_browser, launch_browser_login
from .config import Paths
from .theme import STYLE


class AuthDialog(QtWidgets.QDialog):
    """RiftLift-owned shell around Meta's hosted browser authentication."""

    def __init__(self, paths: Paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.browser = None
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
            "RiftLift opens your default browser in a dedicated sign-in window "
            "and returns here automatically when Meta finishes. Your password "
            "and security codes go only to Meta."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.status = QtWidgets.QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.retry = QtWidgets.QPushButton("Open default browser")
        self.retry.setObjectName("primary")
        self.retry.clicked.connect(self.start)
        layout.addWidget(self.retry)

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
        if not is_signed_in(self.paths):
            QtCore.QTimer.singleShot(0, self.start)

    def show_state(self):
        signed_in = is_signed_in(self.paths)
        self.status.setText(
            "RiftLift is signed in to Meta."
            if signed_in
            else "Opening your default browser…"
        )
        self.retry.setVisible(False)
        self.reset.setVisible(signed_in)

    def start(self):
        self.stop_browser()
        try:
            browser = default_browser()
            sign_out(self.paths)
            self.process = launch_browser_login(self.paths, browser)
        except Exception as error:
            self.status.setText(str(error))
            self.retry.setText("Try again")
            self.retry.setVisible(True)
            self.reset.setVisible(False)
            return
        self.browser = browser
        self.status.setText(f"Waiting for Meta in {browser.name}…")
        self.retry.setVisible(False)
        self.reset.setText("Cancel sign-in")
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
                    "The browser closed before sign-in finished. Try again when ready."
                )
                self.retry.setText("Try again")
                self.retry.setVisible(True)
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
        self.browser = None
        self.reset.setText("Sign out and reset")
        self.reset.setVisible(False)
        self.retry.setText("Open default browser")
        self.retry.setVisible(True)
        self.status.setText("Signed out. Open your default browser when ready.")

    def reject(self):
        self.timer.stop()
        self.stop_browser()
        super().reject()
