from pathlib import Path
from types import SimpleNamespace
import json

from riftlift.auth import complete_browser_login, sign_out
from riftlift.auth_browser import (
    Browser,
    META_LOGIN_URL,
    browser_home,
    default_browser,
    launch_browser_login,
    stop_browser,
    _command_uses_profile,
)
from riftlift.config import Paths


def paths_in(tmp_path: Path) -> Paths:
    return Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )


def test_auth_browser_debug_override_is_deterministic(monkeypatch) -> None:
    monkeypatch.setenv("RIFTLIFT_AUTH_BROWSER", "edge")
    expected = Browser("edge", "Microsoft Edge", "chromium", ("edge",))
    monkeypatch.setattr("riftlift.auth_browser._alias_browser", lambda _alias: expected)

    assert default_browser() == expected


def test_detects_browser_from_the_system_desktop_launcher(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("RIFTLIFT_AUTH_BROWSER", raising=False)
    (tmp_path / "org.mozilla.firefox.desktop").write_text(
        "[Desktop Entry]\n"
        "Name=Firefox\n"
        "Exec=/usr/bin/flatpak run --file-forwarding org.mozilla.firefox @@u %u @@\n"
    )
    monkeypatch.setattr(
        "riftlift.auth_browser._application_directories", lambda: [tmp_path]
    )
    monkeypatch.setattr(
        "riftlift.auth_browser.shutil.which", lambda _name: "/usr/bin/xdg-settings"
    )
    monkeypatch.setattr(
        "riftlift.auth_browser.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="org.mozilla.firefox.desktop\n", returncode=0
        ),
    )
    browser = default_browser()

    assert browser.name == "Firefox"
    assert browser.family == "firefox"
    assert browser.command == (
        "/usr/bin/flatpak",
        "run",
        "org.mozilla.firefox",
    )


def test_arbitrary_chromium_fork_uses_its_desktop_launcher(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("RIFTLIFT_AUTH_BROWSER", raising=False)
    (tmp_path / "com.example.Nova.desktop").write_text(
        "[Desktop Entry]\nName=Nova Browser\nExec=/opt/nova/Nova.AppImage %U\n"
    )
    monkeypatch.setattr(
        "riftlift.auth_browser._application_directories", lambda: [tmp_path]
    )
    monkeypatch.setattr(
        "riftlift.auth_browser.shutil.which", lambda _name: "/usr/bin/xdg-settings"
    )
    monkeypatch.setattr(
        "riftlift.auth_browser.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="com.example.Nova.desktop\n", returncode=0
        ),
    )

    browser = default_browser()

    assert browser.name == "Nova Browser"
    assert browser.family == "chromium"
    assert browser.command == ("/opt/nova/Nova.AppImage",)


def test_edge_login_uses_an_isolated_riftlift_profile(
    tmp_path: Path, monkeypatch
) -> None:
    paths = paths_in(tmp_path)
    launched = []
    browser = Browser("edge", "Microsoft Edge", "chromium", ("edge",))
    monkeypatch.setattr(
        "riftlift.auth_browser.subprocess.Popen",
        lambda command, **options: launched.append((command, options)) or object(),
    )

    launch_browser_login(paths, browser)

    command, options = launched[0]
    assert command[0] == "edge"
    assert f"--user-data-dir={browser_home(paths, browser) / 'profile'}" in command
    assert command[-1] == META_LOGIN_URL
    assert options["start_new_session"] is True
    preferences = json.loads(
        (browser_home(paths, browser) / "profile/Default/Preferences").read_text()
    )
    assert preferences["protocol_handler"]["allowed_origin_protocol_pairs"][
        "https://auth.meta.com"
    ]["oculus"]


def test_firefox_login_uses_an_isolated_riftlift_profile(
    tmp_path: Path, monkeypatch
) -> None:
    paths = paths_in(tmp_path)
    launched = []
    browser = Browser("firefox", "Firefox", "firefox", ("firefox",))
    monkeypatch.setattr(
        "riftlift.auth_browser.subprocess.Popen",
        lambda command, **options: launched.append((command, options)) or object(),
    )

    launch_browser_login(paths, browser)

    command, _options = launched[0]
    assert command[:2] == ["firefox", "--no-remote"]
    assert str(browser_home(paths, browser) / "profile") in command[3]
    assert command[-1] == META_LOGIN_URL
    preferences = (browser_home(paths, browser) / "profile/user.js").read_text()
    assert 'user_pref("browser.aboutwelcome.enabled", false);' in preferences
    assert (
        'user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);'
        in preferences
    )
    assert (
        'user_pref("network.protocol-handler.warn-external.oculus", false);'
        in preferences
    )


def test_flatpak_browser_can_access_only_its_auth_profile(
    tmp_path: Path, monkeypatch
) -> None:
    paths = paths_in(tmp_path)
    launched = []
    browser = Browser(
        "firefox",
        "Firefox",
        "firefox",
        ("/usr/bin/flatpak", "run", "org.mozilla.firefox"),
    )
    monkeypatch.setattr(
        "riftlift.auth_browser.subprocess.Popen",
        lambda command, **_options: launched.append(command) or object(),
    )

    launch_browser_login(paths, browser)

    assert launched[0][2] == f"--filesystem={browser_home(paths, browser)}"
    assert launched[0][3] == "org.mozilla.firefox"


def test_snap_browser_profile_is_isolated_and_resettable(
    tmp_path: Path, monkeypatch
) -> None:
    paths = paths_in(tmp_path / "workspace")
    launched = []
    browser = Browser(
        "firefox_firefox", "Firefox", "firefox", ("snap", "run", "firefox")
    )
    monkeypatch.setattr("riftlift.auth_browser.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "riftlift.auth_browser.subprocess.Popen",
        lambda command, **_options: launched.append(command) or object(),
    )

    launch_browser_login(paths, browser)

    profile = tmp_path / "snap/firefox/common/riftlift-auth/firefox_firefox"
    assert str(profile) in launched[0]
    assert profile.is_dir()
    sign_out(paths)
    assert not profile.exists()


def test_other_chromium_browsers_use_a_readable_isolated_profile(
    tmp_path: Path, monkeypatch
) -> None:
    paths = paths_in(tmp_path)
    launched = []
    browser = Browser("brave", "Brave", "chromium", ("brave",))
    monkeypatch.setattr(
        "riftlift.auth_browser.subprocess.Popen",
        lambda command, **_options: launched.append(command) or object(),
    )

    launch_browser_login(paths, browser)

    assert f"--user-data-dir={browser_home(paths, browser) / 'profile'}" in launched[0]


def test_browser_login_imports_and_protects_the_token(
    tmp_path: Path, monkeypatch
) -> None:
    paths = paths_in(tmp_path)
    token = "FRL" + "a" * 176
    session = SimpleNamespace(complete=lambda: token)

    assert complete_browser_login(paths, session) == token
    target = paths.config / "meta-access-token"
    assert target.read_text().strip() == token
    assert target.stat().st_mode & 0o777 == 0o600


def test_sign_out_removes_only_riftlift_auth_state(tmp_path: Path) -> None:
    paths = paths_in(tmp_path)
    paths.create()
    token = paths.config / "meta-access-token"
    token.write_text("secret")
    browser = Browser("edge", "Microsoft Edge", "chromium", ("edge",))
    cookie = browser_home(paths, browser) / "profile/Default/Network/Cookies"
    cookie.parent.mkdir(parents=True)
    cookie.write_text("cookie")
    unrelated = paths.config / "settings.json"
    unrelated.write_text("keep")

    sign_out(paths)

    assert not token.exists()
    assert not (paths.config / "auth").exists()
    assert unrelated.read_text() == "keep"


def test_stopping_login_closes_detached_profile_processes(
    tmp_path: Path, monkeypatch
) -> None:
    paths = paths_in(tmp_path)
    browser = Browser("firefox", "Firefox", "firefox", ("firefox",))
    stopped = []
    process = SimpleNamespace(poll=lambda: 0)
    monkeypatch.setattr(
        "riftlift.auth_browser._profile_processes", lambda _profile: [123]
    )
    monkeypatch.setattr(
        "riftlift.auth_browser.os.kill",
        lambda pid, signal: stopped.append((pid, signal)),
    )

    stop_browser(paths, browser, process)

    assert stopped and stopped[0][0] == 123


def test_profile_process_matching_supports_firefox_and_chromium() -> None:
    profile = b"/tmp/riftlift/profile"

    assert _command_uses_profile([b"firefox", b"--profile", profile], profile)
    assert _command_uses_profile([b"chromium", b"--user-data-dir=" + profile], profile)
    assert not _command_uses_profile(
        [b"chromium", b"--user-data-dir=/tmp/regular-profile"], profile
    )
