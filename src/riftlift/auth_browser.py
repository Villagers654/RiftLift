"""Default-browser discovery and isolated profiles for Meta authentication."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Paths
from .util import RiftLiftError

META_LOGIN_URL = "https://auth.meta.com/"
_BROWSER_NAMES = {
    "edge": "Microsoft Edge",
    "chrome": "Google Chrome",
    "chromium": "Chromium",
    "brave": "Brave",
    "vivaldi": "Vivaldi",
    "opera": "Opera",
    "firefox": "Firefox",
}
_CHROMIUM_COMMANDS = {
    "edge": (("microsoft-edge", "microsoft-edge-stable"), "com.microsoft.Edge"),
    "chrome": (("google-chrome", "google-chrome-stable"), "com.google.Chrome"),
    "chromium": (("chromium", "chromium-browser"), "org.chromium.Chromium"),
    "brave": (("brave-browser", "brave"), "com.brave.Browser"),
    "vivaldi": (("vivaldi", "vivaldi-stable"), "com.vivaldi.Vivaldi"),
    "opera": (("opera",), "com.opera.Opera"),
}


@dataclass(frozen=True, slots=True)
class Browser:
    key: str
    name: str
    family: str
    command: tuple[str, ...]


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


def _alias_browser(alias: str) -> Browser | None:
    if alias in _CHROMIUM_COMMANDS:
        executables, application_id = _CHROMIUM_COMMANDS[alias]
        for executable in executables:
            if command := shutil.which(executable):
                return Browser(alias, _BROWSER_NAMES[alias], "chromium", (command,))
        if _flatpak_installed(application_id):
            return Browser(
                alias,
                _BROWSER_NAMES[alias],
                "chromium",
                (shutil.which("flatpak") or "flatpak", "run", application_id),
            )
    elif alias == "firefox":
        if command := shutil.which("firefox"):
            return Browser("firefox", "Firefox", "firefox", (command,))
        if _flatpak_installed("org.mozilla.firefox"):
            return Browser(
                "firefox",
                "Firefox",
                "firefox",
                (shutil.which("flatpak") or "flatpak", "run", "org.mozilla.firefox"),
            )
    return None


def _application_directories() -> list[Path]:
    home = Path.home()
    directories = [
        Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "applications",
        home / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
        Path("/var/lib/snapd/desktop/applications"),
    ]
    directories.extend(
        Path(item) / "applications"
        for item in os.environ.get(
            "XDG_DATA_DIRS", "/usr/local/share:/usr/share"
        ).split(":")
        if item
    )
    return directories


def _desktop_browser(desktop_id: str) -> Browser:
    desktop_name = Path(desktop_id).name
    if desktop_name != desktop_id or not desktop_name.endswith(".desktop"):
        raise RiftLiftError("the default browser desktop entry is invalid")
    desktop = next(
        (
            root / desktop_name
            for root in _application_directories()
            if (root / desktop_name).is_file()
        ),
        None,
    )
    if desktop is None:
        raise RiftLiftError(
            f"could not find the default browser launcher {desktop_name}"
        )

    name = desktop.stem
    command_line = ""
    in_desktop_entry = False
    for raw_line in desktop.read_text(errors="replace").splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            in_desktop_entry = line == "[Desktop Entry]"
        elif in_desktop_entry and line.startswith("Name=") and name == desktop.stem:
            name = line.removeprefix("Name=").strip() or name
        elif in_desktop_entry and line.startswith("Exec=") and not command_line:
            command_line = line.removeprefix("Exec=").strip()
    if not command_line:
        raise RiftLiftError(f"the browser launcher {desktop_name} has no command")
    command = []
    for token in shlex.split(command_line):
        if token == "--file-forwarding" or token.startswith("@@"):
            continue
        token = re.sub(r"%[fFuUdDnNickvm]", "", token)
        if token:
            command.append(token)
    if not command:
        raise RiftLiftError(f"the browser launcher {desktop_name} has no executable")
    key = re.sub(r"[^a-z0-9_.-]+", "-", desktop.stem.lower()).strip("-.")
    identity = " ".join((desktop_name, name, *command)).lower()
    family = "firefox" if "firefox" in identity else "chromium"
    return Browser(key or "default", name, family, tuple(command))


def default_browser() -> Browser:
    """Resolve the actual default browser or a deterministic test override."""
    override = os.environ.get("RIFTLIFT_AUTH_BROWSER", "").strip().lower()
    if override:
        if override.endswith(".desktop"):
            return _desktop_browser(override)
        if browser := _alias_browser(override):
            return browser
        raise RiftLiftError(
            "RIFTLIFT_AUTH_BROWSER must be a browser alias or desktop-file ID"
        )
    xdg_settings = shutil.which("xdg-settings")
    if xdg_settings is None:
        raise RiftLiftError("could not detect the system default browser")
    result = subprocess.run(
        (xdg_settings, "get", "default-web-browser"),
        capture_output=True,
        text=True,
        check=False,
    )
    desktop_id = result.stdout.strip()
    if not desktop_id:
        raise RiftLiftError("no system default browser is configured")
    return _desktop_browser(desktop_id)


def browser_home(paths: Paths, browser: Browser) -> Path:
    return paths.config / "auth" / browser.key


def _snap_application(command: tuple[str, ...]) -> str:
    for index, token in enumerate(command):
        if (
            Path(token).name == "snap"
            and index + 2 < len(command)
            and command[index + 1] == "run"
        ):
            return command[index + 2]
        if "/snap/bin/" in token:
            return Path(token).name
    return ""


def _link_cookie_profile(home: Path, profile: Path, family: str) -> None:
    cookie_home = home / "cookie-home"
    if family == "firefox":
        link = cookie_home / ".mozilla/firefox/riftlift.default"
    else:
        link = cookie_home / ".config/chromium"
    link.parent.mkdir(parents=True, exist_ok=True)
    if not link.exists() and not link.is_symlink():
        link.symlink_to(profile, target_is_directory=True)


def _prepare_firefox_profile(profile: Path) -> None:
    """Disable Firefox onboarding inside RiftLift's disposable profile."""
    preferences = {
        "browser.aboutwelcome.enabled": False,
        "browser.shell.checkDefaultBrowser": False,
        "browser.startup.firstrunSkipsHomepage": True,
        "browser.startup.homepage_override.mstone": "ignore",
        "datareporting.policy.dataSubmissionPolicyBypassNotification": True,
        "datareporting.policy.firstRunURL": "",
        "trailhead.firstrun.didSeeAboutWelcome": True,
    }
    lines = []
    for name, value in preferences.items():
        literal = str(value).lower() if isinstance(value, bool) else f'"{value}"'
        lines.append(f'user_pref("{name}", {literal});')
    (profile / "user.js").write_text("\n".join(lines) + "\n")


def launch_browser_login(paths: Paths, browser: Browser) -> subprocess.Popen[bytes]:
    """Open Meta's hosted login in a RiftLift-owned, isolated browser profile."""
    home = browser_home(paths, browser)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.chmod(0o700)
    if snap_application := _snap_application(browser.command):
        profile = (
            Path.home()
            / "snap"
            / snap_application
            / "common/riftlift-auth"
            / browser.key
        )
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        (home / "external-profile").symlink_to(profile, target_is_directory=True)
    else:
        profile = home / "profile"
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)

    launch_command = list(browser.command)
    flatpak_run = next(
        (
            index
            for index, token in enumerate(launch_command[:-1])
            if Path(token).name == "flatpak" and launch_command[index + 1] == "run"
        ),
        None,
    )
    if flatpak_run is not None:
        launch_command.insert(flatpak_run + 2, f"--filesystem={home}")
    _link_cookie_profile(home, profile, browser.family)
    if browser.family == "chromium":
        arguments = [
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            META_LOGIN_URL,
        ]
    else:
        _prepare_firefox_profile(profile)
        arguments = ["--no-remote", "--profile", str(profile), META_LOGIN_URL]
    return subprocess.Popen(
        [*launch_command, *arguments],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def cleanup_browser_profiles(paths: Paths) -> None:
    """Remove only browser profiles created for RiftLift authentication."""
    for marker in (paths.config / "auth").glob("*/external-profile"):
        try:
            target = marker.resolve(strict=True)
            expected = Path.home() / "snap"
            if expected in target.parents and "riftlift-auth" in target.parts:
                shutil.rmtree(target, ignore_errors=True)
        except OSError:
            pass
    shutil.rmtree(paths.config / "auth", ignore_errors=True)
