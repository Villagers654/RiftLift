import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

if os.name != "nt":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

from riftlift.auth import accounts
from riftlift.auth_browser import Browser
from riftlift.auth_ui import MANUAL_SIGN_IN_GUIDE, AuthDialog
from riftlift.config import Paths
from riftlift.util import RiftLiftError

LOGIN_URL = "https://auth.meta.com/native_sso/confirm?native_sso_etoken=abc"
FIREFOX = Browser("firefox", "Firefox", "firefox", ("firefox",))


def _paths(tmp_path: Path) -> Paths:
    paths = Paths(
        *(
            tmp_path / name
            for name in ("data", "cache", "config", "games", "prefix", "tools")
        )
    )
    paths.create()
    return paths


def _wait_until(app, condition, timeout: float = 2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def _no_browser():
    raise RiftLiftError("could not detect the system default browser")


def _browser_opens(*_args):
    return SimpleNamespace(poll=lambda: None)


def _waiting(tmp_path, monkeypatch, *, browser, launch=_browser_opens, token=None):
    """A sign-in waiting for Meta, shown like in the app (window open first)."""
    paths = _paths(tmp_path)
    callback = paths.config / "meta-auth-callback"
    session = SimpleNamespace(
        login_url=LOGIN_URL,
        callback_ready=callback.is_file,
        complete=lambda: token,
        accepts=lambda url: "earlier" not in url,
    )
    monkeypatch.setattr("riftlift.auth_ui.QtCore.QTimer.singleShot", lambda *_: None)
    monkeypatch.setattr("riftlift.auth_ui.default_browser", browser)
    monkeypatch.setattr("riftlift.auth_ui.launch_browser_login", launch)
    monkeypatch.setattr(
        "riftlift.auth_ui.MetaAuthSession.begin", lambda _paths: session
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = AuthDialog(paths)
    dialog.show()
    app.processEvents()
    dialog.start()
    assert _wait_until(
        app, lambda: dialog.pending is not None and dialog.pending.done()
    )
    dialog.check_login()
    app.processEvents()
    assert dialog.operation == "waiting"
    return app, dialog, paths


def _close(app, dialog):
    dialog.close()
    app.processEvents()


def test_manual_sign_in_waits_behind_one_button_when_the_browser_opened(
    tmp_path, monkeypatch
) -> None:
    app, dialog, _ = _waiting(tmp_path, monkeypatch, browser=lambda: FIREFOX)

    assert dialog.status.text() == "Waiting for Meta in Firefox…"
    assert dialog.manual_toggle.isVisible()
    assert dialog.manual_toggle.text() == "Sign in manually"
    assert not dialog.manual_section.isVisible()

    dialog.manual_toggle.click()
    assert dialog.manual_section.isVisible()
    assert not dialog.manual_toggle.isVisible()
    assert dialog.link.text() == LOGIN_URL
    assert dialog.link.isReadOnly()
    _close(app, dialog)


def test_manual_sign_in_opens_by_itself_without_a_browser(
    tmp_path, monkeypatch
) -> None:
    launched = []
    app, dialog, _ = _waiting(
        tmp_path,
        monkeypatch,
        browser=_no_browser,
        launch=lambda *args: launched.append(args),
    )

    assert not launched
    assert dialog.timer.isActive()
    assert dialog.status.text() == (
        "No default browser was found. Sign in manually below."
    )
    assert dialog.manual_section.isVisible()
    assert not dialog.manual_toggle.isVisible()
    _close(app, dialog)


def test_manual_sign_in_opens_when_the_browser_fails_to_start(
    tmp_path, monkeypatch
) -> None:
    def broken(*_args):
        raise OSError("no such file")

    app, dialog, _ = _waiting(
        tmp_path, monkeypatch, browser=lambda: FIREFOX, launch=broken
    )

    assert "Could not open the browser" in dialog.status.text()
    assert dialog.manual_section.isVisible()
    _close(app, dialog)


def test_a_browser_that_exits_with_an_error_opens_manual_sign_in(
    tmp_path, monkeypatch
) -> None:
    app, dialog, _ = _waiting(
        tmp_path,
        monkeypatch,
        browser=lambda: FIREFOX,
        launch=lambda *_: SimpleNamespace(poll=lambda: 1),
    )
    dialog.check_login()

    assert dialog.operation == "waiting"
    assert dialog.manual_section.isVisible()
    _close(app, dialog)


def test_copy_link_puts_it_on_the_clipboard(tmp_path, monkeypatch) -> None:
    app, dialog, _ = _waiting(tmp_path, monkeypatch, browser=_no_browser)

    dialog.copy_link.click()

    assert QtGui.QGuiApplication.clipboard().text() == LOGIN_URL
    assert dialog.copy_link.text() == "Copied"
    dialog._copied_timer.timeout.emit()
    assert dialog.copy_link.text() == "Copy link"
    _close(app, dialog)


def test_a_pasted_callback_finishes_the_sign_in(tmp_path, monkeypatch) -> None:
    token = "FRL" + "b" * 176
    app, dialog, paths = _waiting(
        tmp_path, monkeypatch, browser=_no_browser, token=token
    )

    dialog.callback_entry.setText("  oculus://login?token=abc&blob=xyz  ")
    dialog.finish_button.click()
    assert dialog.operation == "complete"
    assert _wait_until(
        app, lambda: dialog.pending is not None and dialog.pending.done()
    )
    dialog.check_login()

    assert dialog.completed
    assert [account.token for account in accounts(paths)] == [token]
    assert not dialog.manual_section.isVisible()
    _close(app, dialog)


def test_a_pasted_address_that_is_not_meta_s_callback_is_refused(
    tmp_path, monkeypatch
) -> None:
    app, dialog, paths = _waiting(tmp_path, monkeypatch, browser=_no_browser)

    dialog.callback_entry.setText("https://auth.meta.com/native_sso/confirm")
    dialog.finish_button.click()

    assert dialog.operation == "waiting"
    assert dialog.paste_error.isVisible()
    assert dialog.paste_error.text() == (
        "That isn't the address from Meta. It starts with oculus://."
    )
    assert not (paths.config / "meta-auth-callback").exists()
    _close(app, dialog)


def test_the_help_link_opens_the_manual_sign_in_guide(tmp_path, monkeypatch) -> None:
    opened = []
    monkeypatch.setattr(
        "riftlift.auth_ui.QtGui.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()),
    )
    app, dialog, _ = _waiting(tmp_path, monkeypatch, browser=_no_browser)

    dialog.help_link.click()

    assert opened == [MANUAL_SIGN_IN_GUIDE]
    assert MANUAL_SIGN_IN_GUIDE.endswith("/docs/MANUAL_SIGN_IN.md")
    _close(app, dialog)


def test_manual_sign_in_goes_away_on_cancel(tmp_path, monkeypatch) -> None:
    app, dialog, _ = _waiting(tmp_path, monkeypatch, browser=_no_browser)
    dialog.callback_entry.setText("oculus://half-pasted")

    dialog.cancel_login()

    assert not dialog.manual_section.isVisible()
    assert not dialog.manual_toggle.isVisible()
    assert dialog.link.text() == ""
    assert dialog.callback_entry.text() == ""
    _close(app, dialog)


def test_wrapped_dialog_text_is_never_clipped(tmp_path, monkeypatch) -> None:
    app, dialog, _ = _waiting(tmp_path, monkeypatch, browser=_no_browser)
    dialog.callback_entry.setText("https://not-it")
    dialog.finish_button.click()
    app.processEvents()

    for label in dialog.findChildren(QtWidgets.QLabel):
        if label.isVisible() and label.wordWrap():
            assert label.height() >= label.heightForWidth(label.width()), label.text()
    _close(app, dialog)


@pytest.mark.parametrize("browser_fails", (False, True))
def test_wrapped_dialog_text_gets_no_extra_space(
    tmp_path, monkeypatch, browser_fails
) -> None:
    def broken(*_args):
        raise OSError("no such file")

    app, dialog, _ = _waiting(
        tmp_path,
        monkeypatch,
        browser=(lambda: FIREFOX) if browser_fails else _no_browser,
        launch=broken,
    )
    app.processEvents()

    for label in dialog.findChildren(QtWidgets.QLabel):
        if label.isVisible() and label.wordWrap():
            assert label.height() == label.heightForWidth(label.width()), label.text()
    _close(app, dialog)


def test_fitting_the_dialog_logs_no_qt_warnings(tmp_path, monkeypatch) -> None:
    warnings = []
    previous = QtCore.qInstallMessageHandler(
        lambda _kind, _context, message: warnings.append(message)
    )
    try:
        app, dialog, _ = _waiting(tmp_path, monkeypatch, browser=lambda: FIREFOX)
        dialog.manual_toggle.click()
        app.processEvents()
    finally:
        QtCore.qInstallMessageHandler(previous)

    assert not [message for message in warnings if "Negative sizes" in message]
    _close(app, dialog)


def test_a_whole_console_message_can_be_pasted(tmp_path, monkeypatch) -> None:
    messages = {
        "chrome": "Failed to launch 'oculus://login?token=abc&blob=xyz' because "
        "the scheme does not have a registered handler.",
        "firefox": "Prevented navigation to “oculus://login?token=abc&blob=xyz"
        "” due to an unknown protocol. 127.0.0.1:8765",
    }
    for browser, message in messages.items():
        app, dialog, paths = _waiting(
            tmp_path / browser,
            monkeypatch,
            browser=_no_browser,
            token="FRL" + "c" * 176,
        )
        dialog.callback_entry.setText(message)
        dialog.finish_button.click()

        assert dialog.operation == "complete", browser
        callback = (paths.config / "meta-auth-callback").read_text()
        assert callback == "oculus://login?token=abc&blob=xyz", browser
        _close(app, dialog)


def test_the_dialog_shrinks_again_when_manual_sign_in_closes(
    tmp_path, monkeypatch
) -> None:
    app, dialog, _ = _waiting(tmp_path, monkeypatch, browser=lambda: FIREFOX)
    collapsed = dialog.height()
    dialog.manual_toggle.click()
    app.processEvents()
    assert dialog.height() > collapsed

    dialog.cancel_login()
    app.processEvents()

    # Only the signed-out text and one button are left: no leftover tall window.
    assert dialog.height() <= collapsed
    assert dialog.height() == dialog.sizeHint().height()
    _close(app, dialog)


def test_a_callback_from_an_earlier_sign_in_is_refused_at_once(
    tmp_path, monkeypatch
) -> None:
    from riftlift.auth_ui import AUTH

    app, dialog, paths = _waiting(tmp_path, monkeypatch, browser=_no_browser)
    dialog.callback_entry.setText("oculus://earlier-sign-in?blob=x")
    dialog.finish_button.click()
    app.processEvents()

    assert dialog.paste_error.text() == AUTH("paste_old_callback")
    assert dialog.paste_error.isVisible()
    assert dialog.operation == "waiting"
    assert dialog.manual_section.isVisible()
    assert not (paths.config / "meta-auth-callback").exists()
    _close(app, dialog)


def test_the_session_accepts_only_its_own_callback(tmp_path) -> None:
    import hashlib

    from riftlift.meta_auth import MetaAuthSession

    session = MetaAuthSession(_paths(tmp_path), "request-1", LOGIN_URL)
    own = hashlib.sha256(b"request-1").hexdigest()[:16]
    other = hashlib.sha256(b"request-0").hexdigest()[:16]

    assert session.accepts(f"oculus://login?token={own}&blob=b")
    assert not session.accepts(f"oculus://login?token={other}&blob=b")
    assert not session.accepts(f"https://login?token={own}&blob=b")


def test_the_guide_address_is_copied_when_no_browser_opens(
    tmp_path, monkeypatch
) -> None:
    from riftlift.auth_ui import AUTH

    monkeypatch.setattr(
        "riftlift.auth_ui.QtGui.QDesktopServices.openUrl", lambda _url: False
    )
    app, dialog, _ = _waiting(tmp_path, monkeypatch, browser=_no_browser)

    dialog.help_link.click()
    app.processEvents()

    assert QtGui.QGuiApplication.clipboard().text() == MANUAL_SIGN_IN_GUIDE
    assert dialog.paste_error.text() == AUTH("guide_copied").format(
        url=MANUAL_SIGN_IN_GUIDE
    )
    assert dialog.paste_error.isVisible()
    _close(app, dialog)


@pytest.mark.parametrize(
    "browser", [_no_browser, lambda: FIREFOX], ids=["no browser", "browser"]
)
def test_the_guide_address_is_copied_even_when_opening_it_reports_success(
    tmp_path, monkeypatch, browser
) -> None:
    from riftlift.auth_ui import AUTH

    # The desktop portal answers success even when nothing opens https links.
    monkeypatch.setattr(
        "riftlift.auth_ui.QtGui.QDesktopServices.openUrl", lambda _url: True
    )
    app, dialog, _ = _waiting(tmp_path, monkeypatch, browser=browser)
    QtGui.QGuiApplication.clipboard().clear()
    dialog.manual_toggle.click()

    dialog.help_link.click()
    app.processEvents()

    assert QtGui.QGuiApplication.clipboard().text() == MANUAL_SIGN_IN_GUIDE
    assert dialog.paste_error.text() == AUTH("guide_copied").format(
        url=MANUAL_SIGN_IN_GUIDE
    )
    assert dialog.paste_error.isVisible()
    _close(app, dialog)


def test_text_fits_again_when_the_dialog_is_narrowed(tmp_path, monkeypatch) -> None:
    from riftlift.i18n import set_language

    set_language("fr")
    try:
        app, dialog, _ = _waiting(tmp_path, monkeypatch, browser=_no_browser)
        dialog.show_error(RiftLiftError("no browser"))
        app.processEvents()
        dialog.resize(dialog.minimumWidth(), dialog.height())
        app.processEvents()

        for label in dialog.findChildren(QtWidgets.QLabel):
            if label.isVisible() and label.wordWrap():
                assert label.height() >= label.heightForWidth(label.width()), (
                    label.text()
                )
        _close(app, dialog)
    finally:
        set_language("en")
