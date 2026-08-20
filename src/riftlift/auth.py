"""Meta token persistence for RiftLift's native-SSO flow."""

from __future__ import annotations

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
from .util import RiftLiftError, atomic_write_text

_TOKEN_PATTERN = re.compile(rb"[A-Za-z0-9_.|-]{32,4096}")


def complete_browser_login(paths: Paths, session: MetaAuthSession) -> str:
    """Finish Meta native SSO and persist the resulting Oculus profile token."""
    token = session.complete()
    save_access_token(paths, token)
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
    try:
        while True:
            if session.callback_ready():
                complete_browser_login(paths, session)
                print("RiftLift is signed in to Meta.")
                return 0
            if process.poll() is not None:
                raise RiftLiftError("the browser closed before Meta sign-in finished")
            time.sleep(1)
    finally:
        stop_browser(paths, browser, process)


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


def save_access_token(paths: Paths, token: str) -> None:
    """Persist a token only after the active login owner accepts it."""
    paths.create()
    atomic_write_text(paths.config / "meta-access-token", token + "\n")


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
