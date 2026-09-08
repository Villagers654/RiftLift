"""Default-browser discovery and launch for Meta authentication."""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Paths, xdg_data_dirs, xdg_data_home
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
    try:
        result = subprocess.run(
            (flatpak, "info", application_id),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


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
        xdg_data_home() / "applications",
        home / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
        Path("/var/lib/snapd/desktop/applications"),
    ]
    directories.extend(path / "applications" for path in xdg_data_dirs())
    return directories


def _desktop_entry(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    active = False
    for raw_line in path.read_text(errors="replace").splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            active = line == "[Desktop Entry]"
            continue
        if not active or not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and key in {"Name", "Exec", "Icon"} and key not in values:
            values[key] = value.strip()
    return values


def _desktop_command(
    command_line: str, *, name: str, icon: str, desktop: Path
) -> tuple[str, ...]:
    command: list[str] = []
    discarded = {"%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%v", "%m"}
    for token in shlex.split(command_line):
        if token == "--file-forwarding" or token.startswith("@@"):
            continue
        if token in discarded:
            continue
        if token == "%i":
            if icon:
                command.extend(("--icon", icon))
            continue
        token = (
            token.replace("%%", "\0").replace("%c", name).replace("%k", str(desktop))
        )
        if re.search(r"%[A-Za-z]", token):
            raise RiftLiftError(
                "the default browser launcher has an invalid field code"
            )
        command.append(token.replace("\0", "%"))
    return tuple(command)


def _find_desktop_file(desktop_id: str) -> Path | None:
    for root in _application_directories():
        direct = root / desktop_id
        if direct.is_file():
            return direct
        with contextlib.suppress(OSError):
            for candidate in root.rglob("*.desktop"):
                candidate_id = candidate.relative_to(root).as_posix().replace("/", "-")
                if candidate_id == desktop_id:
                    return candidate
    return None


def _desktop_browser(desktop_id: str) -> Browser:
    desktop_name = Path(desktop_id).name
    if desktop_name != desktop_id or not desktop_name.endswith(".desktop"):
        raise RiftLiftError("the default browser desktop entry is invalid")
    desktop = _find_desktop_file(desktop_name)
    if desktop is None:
        raise RiftLiftError(
            f"could not find the default browser launcher {desktop_name}"
        )

    entry = _desktop_entry(desktop)
    name = entry.get("Name") or desktop.stem
    command_line = entry.get("Exec", "")
    if not command_line:
        raise RiftLiftError(f"the browser launcher {desktop_name} has no command")
    command = _desktop_command(
        command_line,
        name=name,
        icon=entry.get("Icon", ""),
        desktop=desktop,
    )
    if not command:
        raise RiftLiftError(f"the browser launcher {desktop_name} has no executable")
    key = re.sub(r"[^a-z0-9_.-]+", "-", desktop.stem.lower()).strip("-.")
    identity = " ".join((desktop_name, name, *command)).lower()
    family = "firefox" if "firefox" in identity else "chromium"
    return Browser(key or "default", name, family, command)


def default_browser() -> Browser:
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
    try:
        result = subprocess.run(
            (xdg_settings, "get", "default-web-browser"),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RiftLiftError("could not query the system default browser") from error
    desktop_id = result.stdout.strip()
    if not desktop_id:
        raise RiftLiftError("no system default browser is configured")
    return _desktop_browser(desktop_id)


def browser_home(paths: Paths, browser: Browser) -> Path:
    return paths.config / "auth" / browser.key


def launch_browser_login(
    paths: Paths, browser: Browser, url: str = META_LOGIN_URL
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [*browser.command, url],
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
