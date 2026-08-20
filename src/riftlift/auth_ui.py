"""Account UI for RiftLift's browser-backed Meta authentication."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

from PySide6 import QtCore, QtWidgets

from .auth import complete_browser_login, is_signed_in, sign_out
from .auth_browser import default_browser, launch_browser_login, stop_browser
from .config import Paths
from .meta_auth import MetaAuthSession
from .theme import STYLE


class AuthDialog(QtWidgets.QDialog):
    """RiftLift-owned shell around Meta's hosted browser authentication."""

    def __init__(self, paths: Paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.browser = None
        self.session = None
        self.process = None
        self.pending: Future | None = None
        self.operation = "idle"
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="meta-auth"
        )
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

        self.reset = QtWidgets.QPushButton("Sign out and reset")
        self.reset.clicked.connect(self.reset_login)
        layout.addWidget(self.reset)

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
        except Exception as error:
            self.show_error(error)
            return
        self.browser = browser
        self.session = None
        self.operation = "begin"
        self.pending = self.executor.submit(MetaAuthSession.begin, self.paths)
        self.status.setText("Preparing a secure Meta sign-in…")
        self.retry.setVisible(False)
        self.reset.setText("Cancel sign-in")
        self.reset.setVisible(True)
        self.timer.start()

    def check_login(self):
        handler = {
            "begin": self._finish_session_start,
            "waiting": self._check_callback,
            "complete": self._finish_login,
        }.get(self.operation)
        if handler is not None:
            handler()

    def _finish_session_start(self):
        if self.pending is None or not self.pending.done():
            return
        try:
            self.session = self.pending.result()
            self.process = launch_browser_login(
                self.paths, self.browser, self.session.login_url
            )
        except Exception as error:
            self.show_error(error)
            return
        self.pending = None
        self.operation = "waiting"
        self.status.setText(f"Waiting for Meta in {self.browser.name}…")

    def _check_callback(self):
        if self.session is not None and self.session.callback_ready():
            self.operation = "complete"
            self.pending = self.executor.submit(
                complete_browser_login, self.paths, self.session
            )
            self.status.setText("Finishing sign-in securely…")
        elif self.process is not None and self.process.poll() is not None:
            self.show_error(
                "The browser closed before sign-in finished. Try again when ready."
            )

    def _finish_login(self):
        if self.pending is None or not self.pending.done():
            return
        try:
            self.pending.result()
        except Exception as error:
            self.show_error(error)
        else:
            self.timer.stop()
            self.operation = "idle"
            self.pending = None
            self.completed = True
            self.status.setText("Signed in. Returning to RiftLift…")
            self.stop_browser()
            QtCore.QTimer.singleShot(500, self.accept)

    def show_error(self, error):
        self.timer.stop()
        self.stop_browser()
        self.pending = None
        self.operation = "idle"
        self.status.setText(str(error))
        self.retry.setText("Try again")
        self.retry.setVisible(True)
        self.reset.setText("Sign out and reset")
        self.reset.setVisible(False)

    def stop_browser(self):
        if self.process is not None and self.browser is not None:
            stop_browser(self.paths, self.browser, self.process)
        self.process = None

    def reset_login(self):
        self.timer.stop()
        self.stop_browser()
        sign_out(self.paths)
        self.browser = None
        self.session = None
        self.pending = None
        self.operation = "idle"
        self.reset.setText("Sign out and reset")
        self.reset.setVisible(False)
        self.retry.setText("Open default browser")
        self.retry.setVisible(True)
        self.status.setText("Signed out. Open your default browser when ready.")

    def reject(self):
        self.timer.stop()
        self.stop_browser()
        self.executor.shutdown(wait=False, cancel_futures=True)
        super().reject()
