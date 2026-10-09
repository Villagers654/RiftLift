"""Account UI for RiftLift's browser-backed Meta authentication."""

from __future__ import annotations

import os
from concurrent.futures import Future, ThreadPoolExecutor

from PySide6 import QtCore, QtWidgets

from .auth import accounts, complete_browser_login, prepare_login, sign_out
from .auth_browser import default_browser, launch_browser_login, stop_browser
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
        self.changed = False
        self.setWindowTitle(AUTH("title"))
        self.setMinimumWidth(520)
        self.setStyleSheet(STYLE)

        layout = wrap_dialog(self, AUTH("title"), margins=(24, 22, 24, 22))
        layout.setSpacing(12)
        title = QtWidgets.QLabel(AUTH("heading"))
        title.setObjectName("game")
        layout.addWidget(title)
        explanation = QtWidgets.QLabel(
            "RiftLift opens your browser and returns here when Meta finishes. "
            "Your password and security codes go only to Meta."
            if os.name == "nt"
            else AUTH("explanation")
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.account_list = QtWidgets.QVBoxLayout()
        self.account_list.setSpacing(6)
        layout.addLayout(self.account_list)

        self.status = QtWidgets.QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.retry = QtWidgets.QPushButton(AUTH("open_browser"))
        self.retry.setObjectName("primary")
        self.retry.clicked.connect(self.start)
        layout.addWidget(self.retry)

        self.reset = QtWidgets.QPushButton(AUTH("sign_out_all"))
        self.reset.clicked.connect(self._reset_clicked)
        layout.addWidget(self.reset)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(900)
        self.timer.timeout.connect(self.check_login)
        self.show_state()
        if not accounts(self.paths):
            self.status.setText(AUTH("opening_browser"))
            self.retry.setVisible(False)
            QtCore.QTimer.singleShot(0, self.start)

    def show_accounts(self):
        """Rebuild one row per signed-in account, each with its own sign-out."""
        while self.account_list.count():
            row = self.account_list.takeAt(0).widget()
            if row is not None:
                row.deleteLater()
        for number, account in enumerate(accounts(self.paths), 1):
            row = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            name = QtWidgets.QLabel(
                account.name or AUTH("unnamed_account").format(number=number)
            )
            row_layout.addWidget(name, 1)
            remove = QtWidgets.QPushButton(AUTH("sign_out"))
            remove.setAccessibleName(AUTH("sign_out_named").format(name=name.text()))
            remove.setProperty("account_id", account.id)
            remove.clicked.connect(self._sign_out_clicked)
            row_layout.addWidget(remove)
            self.account_list.addWidget(row)

    def show_state(self):
        """Show the idle account list and the actions that fit it."""
        self.show_accounts()
        count = len(accounts(self.paths))
        self.status.setText(
            AUTH("signed_out")
            if count == 0
            else AUTH("signed_in")
            if count == 1
            else AUTH("signed_in_many").format(count=count)
        )
        self.retry.setText(AUTH("add_account") if count else AUTH("open_browser"))
        self.retry.setVisible(True)
        self.reset.setText(AUTH("sign_out_all"))
        self.reset.setVisible(count > 1)

    def start(self):
        self.process = None
        try:
            browser = default_browser()
            prepare_login(self.paths)
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
            self.pending = self.executor.submit(
                complete_browser_login, self.paths, self.session
            )
            self.status.setText(AUTH("finishing"))
        elif self.process is not None and self.process.poll() not in (None, 0):
            self.show_error(AUTH("browser_open_failed"))

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
            self.changed = True
            self.show_accounts()
            self.status.setText(AUTH("signed_in_returning"))
            self._stop_browser()
            QtCore.QTimer.singleShot(500, self.accept)

    def show_error(self, error):
        self.timer.stop()
        self._stop_browser()
        self.pending = None
        self.operation = "idle"
        self.status.setText(str(error))
        self.retry.setText(AUTH("try_again"))
        self.retry.setVisible(True)
        self.reset.setVisible(False)

    def _reset_clicked(self):
        if self.operation == "idle":
            self.sign_out_all()
        else:
            self.cancel_login()

    def cancel_login(self):
        """Abandon the sign-in in progress; signed-in accounts stay."""
        self.timer.stop()
        self._stop_browser()
        self.browser = None
        self.session = None
        if self.pending is not None:
            self.pending.cancel()
        self.pending = None
        self.operation = "idle"
        self.show_state()

    def _sign_out_clicked(self):
        self.sign_out_account(self.sender().property("account_id"))

    def sign_out_account(self, account_id: str):
        sign_out(self.paths, account_id)
        self.changed = True
        self.show_state()

    def sign_out_all(self):
        self.cancel_login()
        sign_out(self.paths)
        self.changed = True
        self.show_state()

    def accept(self):
        self.timer.stop()
        self._stop_browser()
        self.executor.shutdown(wait=False, cancel_futures=True)
        super().accept()

    def reject(self):
        self.timer.stop()
        self._stop_browser()
        self.executor.shutdown(wait=False, cancel_futures=True)
        super().reject()

    def _stop_browser(self):
        if os.name == "nt" and self.process is not None and self.browser is not None:
            stop_browser(self.paths, self.browser, self.process)
        self.process = None
