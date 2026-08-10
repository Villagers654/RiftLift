"""Meta token persistence built on RiftLift's isolated browser profiles."""

from __future__ import annotations

import os
import re
from pathlib import Path

from meta_pcvr_downloader.auth import AuthenticationError, get_access_token

from .auth_browser import (
    Browser,
    browser_home,
    cleanup_browser_profiles,
    default_browser,
    launch_browser_login,
)
from .config import Paths
from .util import RiftLiftError

_TOKEN_PATTERN = re.compile(rb"[A-Za-z0-9_.|-]{32,4096}")


def complete_browser_login(paths: Paths, browser: Browser) -> str:
    """Import the Meta session created in RiftLift's managed browser profile."""
    try:
        token = get_access_token(browser_home(paths, browser) / "cookie-home")
    except AuthenticationError as error:
        raise RiftLiftError(str(error)) from error
    _save(paths, token)
    return token


def sign_out(paths: Paths) -> None:
    """Forget RiftLift's token and its isolated browser login profiles."""
    (paths.config / "meta-access-token").unlink(missing_ok=True)
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
