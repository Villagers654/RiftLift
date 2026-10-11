"""Account UI for RiftLift's browser-backed Meta authentication."""

from __future__ import annotations

import os
import re
from concurrent.futures import Future, ThreadPoolExecutor

from PySide6 import QtCore, QtGui, QtWidgets

from .auth import (
    accounts,
    complete_browser_login,
    complete_login,
    prepare_login,
    sign_out,
)
from .auth_browser import default_browser, launch_browser_login, stop_browser
from .config import Paths
from .i18n import namespace
from .meta_auth import MetaAuthSession
from .theme import STYLE
from .titlebar import wrap_dialog

AUTH = namespace("auth")
# The callback inside whatever was pasted: a browser console message copied
# whole carries it between quotes (Chrome) or curly quotes (Firefox).
_CALLBACK_IN_TEXT = re.compile(
    r"oculus(?:-client)?://[^\s'\"\u2018\u2019\u201c\u201d<>]+", re.I
)
MANUAL_SIGN_IN_GUIDE = (
    "https://github.com/Villagers654/RiftLift/blob/main/docs/MANUAL_SIGN_IN.md"
)


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
        self._wrapped = [explanation]

        self.account_list = QtWidgets.QVBoxLayout()
        self.account_list.setSpacing(6)
        layout.addLayout(self.account_list)

        self.status = QtWidgets.QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._wrapped.append(self.status)

        # Manual sign-in, behind one quiet button: open the link in any browser,
        # then paste the oculus:// address Meta sends that browser to. It opens
        # by itself only when RiftLift couldn't open a browser.
        self.manual_toggle = QtWidgets.QPushButton(AUTH("manual_toggle"))
        self.manual_toggle.setObjectName("link")
        self.manual_toggle.setAccessibleName(AUTH("manual_toggle"))
        self.manual_toggle.clicked.connect(self._open_manual)
        self.manual_toggle.hide()
        layout.addWidget(self.manual_toggle, alignment=QtCore.Qt.AlignLeft)

        self.manual_section = QtWidgets.QWidget()
        manual_layout = QtWidgets.QVBoxLayout(self.manual_section)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.setSpacing(6)
        step_open = QtWidgets.QLabel(AUTH("manual_step_open"))
        step_open.setWordWrap(True)
        manual_layout.addWidget(step_open)
        self._wrapped.append(step_open)
        link_row = QtWidgets.QHBoxLayout()
        self.link = QtWidgets.QLineEdit()
        self.link.setReadOnly(True)
        self.link.setAccessibleName(AUTH("link_label"))
        link_row.addWidget(self.link, 1)
        self.copy_link = QtWidgets.QPushButton(AUTH("copy_link"))
        self.copy_link.setAccessibleName(AUTH("copy_link"))
        self.copy_link.clicked.connect(self._copy_link)
        link_row.addWidget(self.copy_link)
        manual_layout.addLayout(link_row)
        manual_layout.addSpacing(8)
        step_paste = QtWidgets.QLabel(AUTH("manual_step_paste"))
        step_paste.setWordWrap(True)
        manual_layout.addWidget(step_paste)
        self._wrapped.append(step_paste)
        paste_row = QtWidgets.QHBoxLayout()
        self.callback_entry = QtWidgets.QLineEdit()
        self.callback_entry.setPlaceholderText("oculus://…")
        self.callback_entry.setAccessibleName(AUTH("paste_label"))
        self.callback_entry.returnPressed.connect(self._submit_callback)
        paste_row.addWidget(self.callback_entry, 1)
        self.finish_button = QtWidgets.QPushButton(AUTH("finish_sign_in"))
        self.finish_button.setAccessibleName(AUTH("finish_sign_in"))
        self.finish_button.clicked.connect(self._submit_callback)
        paste_row.addWidget(self.finish_button)
        manual_layout.addLayout(paste_row)
        self.paste_error = QtWidgets.QLabel()
        self.paste_error.setObjectName("muted")
        self.paste_error.setWordWrap(True)
        self.paste_error.hide()
        manual_layout.addWidget(self.paste_error)
        self._wrapped.append(self.paste_error)
        self.help_link = QtWidgets.QPushButton(AUTH("manual_help"))
        self.help_link.setObjectName("link")
        self.help_link.setAccessibleName(AUTH("manual_help"))
        self.help_link.clicked.connect(self._open_guide)
        manual_layout.addWidget(self.help_link, alignment=QtCore.Qt.AlignLeft)
        self.manual_section.hide()
        layout.addWidget(self.manual_section)
        self._copied_timer = QtCore.QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.setInterval(2000)
        self._copied_timer.timeout.connect(
            lambda: self.copy_link.setText(AUTH("copy_link"))
        )

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # A narrower or wider dialog wraps its text onto a different number of
        # lines. _fit_text only changes the height, so this doesn't repeat.
        if event.size().width() != event.oldSize().width():
            self._fit_text()

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

    def _fit_text(self):
        # A top-level dialog doesn't follow its content: give each wrapped label
        # the height its text needs at the dialog's width, then size the dialog
        # to the content, smaller again once a section is hidden.
        left, _top, right, _bottom = self.layout().getContentsMargins()
        width = max(self.width(), self.minimumWidth()) - left - right
        for label in self._wrapped:
            label.ensurePolished()
            # An empty label answers -1.
            label.setFixedHeight(max(0, label.heightForWidth(width)))
        self.layout().activate()
        height = self.sizeHint().height()
        if height != self.height():
            self.resize(self.width(), height)

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
        self._fit_text()

    def start(self):
        self.process = None
        try:
            prepare_login(self.paths)
        except Exception as error:
            self.show_error(error)
            return
        try:
            browser = default_browser()
        except Exception:
            # Not fatal: the sign-in link still works in any browser.
            browser = None
        self.browser = browser
        self._hide_manual()
        self.session = None
        self.operation = "begin"
        self.pending = self.executor.submit(MetaAuthSession.begin, self.paths)
        self.status.setText(AUTH("preparing"))
        self.retry.setVisible(False)
        self.reset.setText(AUTH("cancel_sign_in"))
        self.reset.setVisible(True)
        self.timer.start()
        self._fit_text()

    def check_login(self):
        handler = {
            "begin": self._finish_session_start,
            "waiting": self._check_callback,
            "complete": self._finish_login,
        }.get(self.operation)
        if handler is not None:
            handler()
        self._fit_text()

    def _finish_session_start(self):
        if self.pending is None or not self.pending.done():
            return
        try:
            self.session = self.pending.result()
        except Exception as error:
            self.show_error(error)
            return
        self.pending = None
        self.operation = "waiting"
        self._offer_manual(self.session.login_url)
        if self.browser is None:
            self.status.setText(AUTH("no_browser"))
            self._open_manual()
            return
        try:
            self.process = launch_browser_login(
                self.paths, self.browser, self.session.login_url
            )
        except Exception:
            self.status.setText(AUTH("browser_open_failed"))
            self._open_manual()
            return
        self.status.setText(AUTH("waiting_for_meta").format(browser=self.browser.name))

    def _check_callback(self):
        if self.session is not None and self.session.callback_ready():
            self.operation = "complete"
            self.pending = self.executor.submit(
                complete_browser_login, self.paths, self.session
            )
            self.status.setText(AUTH("finishing"))
        elif self.process is not None and self.process.poll() not in (None, 0):
            # Keep waiting: signing in manually can still finish it.
            self.process = None
            self.status.setText(AUTH("browser_open_failed"))
            self._open_manual()

    def _offer_manual(self, url: str):
        self.link.setText(url)
        self.link.setCursorPosition(0)
        self.copy_link.setText(AUTH("copy_link"))
        if self.manual_section.isHidden():
            self.manual_toggle.show()
        self._fit_text()

    def _open_manual(self):
        self.manual_toggle.hide()
        self.manual_section.show()
        self._fit_text()

    def _hide_manual(self):
        self.link.clear()
        self.callback_entry.clear()
        self.paste_error.hide()
        self.manual_toggle.hide()
        self.manual_section.hide()
        self._fit_text()

    def _open_guide(self):
        # The result can't be trusted: the desktop portal reports success even when
        # nothing opens https links. Without a browser, the address is the only way.
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(MANUAL_SIGN_IN_GUIDE))
        QtGui.QGuiApplication.clipboard().setText(MANUAL_SIGN_IN_GUIDE)
        self.paste_error.setText(AUTH("guide_copied").format(url=MANUAL_SIGN_IN_GUIDE))
        self.paste_error.show()
        self._fit_text()

    def _submit_callback(self):
        if self.operation != "waiting":
            return
        found = _CALLBACK_IN_TEXT.search(self.callback_entry.text())
        try:
            if found is None:
                raise ValueError(AUTH("paste_not_callback"))
            # Checked here as well, so an address from an earlier attempt
            # doesn't end this one.
            if self.session is not None and not self.session.accepts(found.group(0)):
                raise ValueError(AUTH("paste_old_callback"))
            complete_login(self.paths, found.group(0))
        except Exception as error:
            self.paste_error.setText(str(error))
            self.paste_error.show()
            self._fit_text()
            return
        self.paste_error.hide()
        # The waiting sign-in picks the callback up and verifies it with Meta.
        self.check_login()

    def _copy_link(self):
        QtGui.QGuiApplication.clipboard().setText(self.link.text())
        self.copy_link.setText(AUTH("link_copied"))
        self._copied_timer.start()

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
            self._hide_manual()
            self._stop_browser()
            QtCore.QTimer.singleShot(500, self.accept)

    def show_error(self, error):
        self.timer.stop()
        self._stop_browser()
        self._hide_manual()
        self.pending = None
        self.operation = "idle"
        self.status.setText(str(error))
        self.retry.setText(AUTH("try_again"))
        self.retry.setVisible(True)
        self.reset.setVisible(False)
        self._fit_text()

    def _reset_clicked(self):
        if self.operation == "idle":
            self.sign_out_all()
        else:
            self.cancel_login()

    def cancel_login(self):
        """Abandon the sign-in in progress; signed-in accounts stay."""
        self.timer.stop()
        self._stop_browser()
        self._hide_manual()
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
