from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from meta_pcvr_downloader.auth import AuthenticationError, get_access_token

from .config import Paths
from .util import RiftLiftError

_TOKEN_PATTERN = re.compile(rb"[A-Za-z0-9_.|-]{32,4096}")
META_LOGIN_URL = "https://auth.meta.com/"
SUPPORTED_BROWSERS = ("edge", "firefox")


def _flatpak_installed(application_id: str) -> bool:
    flatpak = shutil.which("flatpak")
    if flatpak is None:
        return False
    return (
        subprocess.run(
            (flatpak, "info", application_id),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def browser_command(browser: str) -> list[str] | None:
    """Return a launch command for a supported host browser."""
    if browser == "edge":
        for executable in ("microsoft-edge", "microsoft-edge-stable"):
            if command := shutil.which(executable):
                return [command]
        if _flatpak_installed("com.microsoft.Edge"):
            return [shutil.which("flatpak") or "flatpak", "run", "com.microsoft.Edge"]
    elif browser == "firefox":
        if command := shutil.which("firefox"):
            return [command]
        if _flatpak_installed("org.mozilla.firefox"):
            return [
                shutil.which("flatpak") or "flatpak",
                "run",
                "org.mozilla.firefox",
            ]
    else:
        raise ValueError(f"unsupported browser {browser!r}")
    return None


def available_browsers() -> list[str]:
    return [browser for browser in SUPPORTED_BROWSERS if browser_command(browser)]


def browser_home(paths: Paths, browser: str) -> Path:
    if browser not in SUPPORTED_BROWSERS:
        raise ValueError(f"unsupported browser {browser!r}")
    return paths.config / "auth" / browser


def launch_browser_login(paths: Paths, browser: str) -> subprocess.Popen[bytes]:
    """Open Meta's hosted login in a RiftLift-owned, isolated browser profile."""
    command = browser_command(browser)
    if command is None:
        raise RiftLiftError(f"{browser.title()} is not installed")
    home = browser_home(paths, browser)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.chmod(0o700)
    if browser == "edge":
        profile = home / ".config/microsoft-edge"
        arguments = [
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            META_LOGIN_URL,
        ]
    else:
        profile = home / ".mozilla/firefox/riftlift.default"
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        (profile / "user.js").write_text(
            'user_pref("browser.shell.checkDefaultBrowser", false);\n'
            'user_pref("browser.startup.firstrunSkipsHomepage", true);\n'
            'user_pref("browser.startup.homepage_override.mstone", "ignore");\n'
            'user_pref("trailhead.firstrun.didSeeAboutWelcome", true);\n'
        )
        arguments = ["--no-remote", "--profile", str(profile), META_LOGIN_URL]
    return subprocess.Popen(
        [*command, *arguments],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def complete_browser_login(paths: Paths, browser: str) -> str:
    """Import the Meta session created in RiftLift's managed browser profile."""
    try:
        token = get_access_token(browser_home(paths, browser))
    except AuthenticationError as error:
        raise RiftLiftError(str(error)) from error
    _save(paths, token)
    return token


def sign_out(paths: Paths) -> None:
    """Forget RiftLift's token and its isolated browser login profiles."""
    (paths.config / "meta-access-token").unlink(missing_ok=True)
    shutil.rmtree(paths.config / "auth", ignore_errors=True)


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
    failures: list[str] = []
    for browser in SUPPORTED_BROWSERS:
        try:
            return complete_browser_login(paths, browser)
        except RiftLiftError as error:
            failures.append(str(error))

    detail = f" Last error: {failures[-1]}" if failures else ""
    raise RiftLiftError(
        "RiftLift is signed out. Open Sign In and continue in Edge or Firefox." + detail
    )
