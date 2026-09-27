"""Account UI for RiftLift's browser-backed Meta authentication."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

from PySide6 import QtCore, QtWidgets

from .auth import is_signed_in, save_access_token, sign_out
from .auth_browser import default_browser, launch_browser_login
from .config import Paths
from .i18n import namespace
from .meta_auth import MetaAuthSession
from .theme import STYLE
from .titlebar import wrap_dialog

AUTH = namespace("auth")


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
        self.setWindowTitle(AUTH("title"))
        self.setMinimumWidth(520)
        self.setStyleSheet(STYLE)

        layout = wrap_dialog(self, AUTH("title"), margins=(24, 22, 24, 22))
        layout.setSpacing(12)
        title = QtWidgets.QLabel(AUTH("heading"))
        title.setObjectName("game")
        layout.addWidget(title)
        explanation = QtWidgets.QLabel(AUTH("explanation"))
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.status = QtWidgets.QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.retry = QtWidgets.QPushButton(AUTH("open_browser"))
        self.retry.setObjectName("primary")
        self.retry.clicked.connect(self.start)
        layout.addWidget(self.retry)

        self.reset = QtWidgets.QPushButton(AUTH("sign_out_reset"))
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
        self.status.setText(AUTH("signed_in") if signed_in else AUTH("opening_browser"))
        self.retry.setVisible(False)
        self.reset.setVisible(signed_in)

    def start(self):
        self.process = None
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
        self.status.setText(AUTH("preparing"))
        self.retry.setVisible(False)
        self.reset.setText(AUTH("cancel_sign_in"))
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
        self.status.setText(AUTH("waiting_for_meta").format(browser=self.browser.name))

    def _check_callback(self):
        if self.session is not None and self.session.callback_ready():
            self.operation = "complete"
            self.pending = self.executor.submit(self.session.complete)
            self.status.setText(AUTH("finishing"))
        elif self.process is not None and self.process.poll() not in (None, 0):
            self.show_error(AUTH("browser_open_failed"))

    def _finish_login(self):
        if self.pending is None or not self.pending.done():
            return
        try:
            token = self.pending.result()
        except Exception as error:
            self.show_error(error)
        else:
            save_access_token(self.paths, token)
            self.timer.stop()
            self.operation = "idle"
            self.pending = None
            self.completed = True
            self.status.setText(AUTH("signed_in_returning"))
            self.process = None
            QtCore.QTimer.singleShot(500, self.accept)

    def show_error(self, error):
        self.timer.stop()
        self.process = None
        self.pending = None
        self.operation = "idle"
        self.status.setText(str(error))
        self.retry.setText(AUTH("try_again"))
        self.retry.setVisible(True)
        self.reset.setText(AUTH("sign_out_reset"))
        self.reset.setVisible(False)

    def reset_login(self):
        self.timer.stop()
        self.process = None
        sign_out(self.paths)
        self.browser = None
        self.session = None
        if self.pending is not None:
            self.pending.cancel()
        self.pending = None
        self.operation = "idle"
        self.reset.setText(AUTH("sign_out_reset"))
        self.reset.setVisible(False)
        self.retry.setText(AUTH("open_browser"))
        self.retry.setVisible(True)
        self.status.setText(AUTH("signed_out"))

    def accept(self):
        self.timer.stop()
        self.process = None
        self.executor.shutdown(wait=False, cancel_futures=True)
        super().accept()

    def reject(self):
        self.timer.stop()
        self.process = None
        self.executor.shutdown(wait=False, cancel_futures=True)
        super().reject()
