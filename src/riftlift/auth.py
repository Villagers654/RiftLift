"""Meta token persistence for RiftLift's native-SSO flow."""

from __future__ import annotations

import os
import re
import time

from .auth_browser import (
    cleanup_browser_profiles,
    default_browser,
    launch_browser_login,
    stop_browser,
)
from .config import Paths
from .meta_auth import MetaAuthSession, clear_callback, record_callback
from .util import RiftLiftError

_TOKEN_PATTERN = re.compile(rb"[A-Za-z0-9_.|-]{32,4096}")


def complete_browser_login(paths: Paths, session: MetaAuthSession) -> str:
    """Finish Meta native SSO and persist the resulting Oculus profile token."""
    token = session.complete()
    _save(paths, token)
    return token


def complete_login(paths: Paths, callback_url: str) -> int:
    """Hand a browser's custom-scheme callback to the active auth session."""
    return record_callback(paths, callback_url)


def login(paths: Paths) -> int:
    """Run the browser-backed sign-in flow for command-line users."""
    browser = default_browser()
    sign_out(paths)
    session = MetaAuthSession.begin(paths)
    process = launch_browser_login(paths, browser, session.login_url)
    print(f"Finish signing in to Meta in {browser.name}.")
    while process.poll() is None:
        if not session.callback_ready():
            time.sleep(1)
            continue
        complete_browser_login(paths, session)
        stop_browser(paths, browser, process)
        print("RiftLift is signed in to Meta.")
        return 0
    raise RiftLiftError("the browser closed before Meta sign-in finished")


def sign_out(paths: Paths) -> None:
    """Forget RiftLift's token and its isolated browser login profiles."""
    (paths.config / "meta-access-token").unlink(missing_ok=True)
    clear_callback(paths)
    cleanup_browser_profiles(paths)


def is_signed_in(paths: Paths) -> bool:
    """Return whether RiftLift has a syntactically valid cached Meta token."""
    try:
        value = (paths.config / "meta-access-token").read_text().strip().encode()
    except (FileNotFoundError, OSError, UnicodeError):
        return False
    return _TOKEN_PATTERN.fullmatch(value) is not None


def _save(paths: Paths, token: str) -> None:
    paths.create()
    target = paths.config / "meta-access-token"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(token + "\n")
    target.chmod(0o600)


def runtime_access_token(paths: Paths, *, refresh: bool = False) -> str:
    """Return the Meta token imported by RiftLift's browser login flow."""
    target = paths.config / "meta-access-token"
    if not refresh:
        try:
            token = target.read_text().strip()
            if _TOKEN_PATTERN.fullmatch(token.encode("ascii")):
                return token
        except (FileNotFoundError, OSError, UnicodeError):
            pass
    raise RiftLiftError(
        "RiftLift is signed out. Open Sign In and finish Meta authentication."
    )
