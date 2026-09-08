from pathlib import Path
from types import SimpleNamespace

import pytest

from riftlift.auth import complete_browser_login, login, runtime_access_token, sign_out
from riftlift.auth_browser import (
    META_LOGIN_URL,
    Browser,
    _desktop_browser,
    browser_home,
    default_browser,
    launch_browser_login,
)
from riftlift.config import Paths
from riftlift.util import RiftLiftError


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


def test_desktop_file_id_resolves_nested_entry_and_standard_field_codes(
    tmp_path: Path, monkeypatch
) -> None:
    launcher = tmp_path / "vendor/browser.desktop"
    launcher.parent.mkdir()
    launcher.write_text(
        "[Desktop Entry]\n"
        "Name=Nested Browser\n"
        "Icon=nested-browser\n"
        "Exec=/opt/browser --name=%c %i %U\n"
    )
    monkeypatch.setattr(
        "riftlift.auth_browser._application_directories", lambda: [tmp_path]
    )

    browser = _desktop_browser("vendor-browser.desktop")

    assert browser.command == (
        "/opt/browser",
        "--name=Nested Browser",
        "--icon",
        "nested-browser",
    )


@pytest.mark.parametrize(
    "command",
    [
        ("firefox",),
        ("edge",),
        ("brave",),
        ("/usr/bin/flatpak", "run", "org.mozilla.firefox"),
        ("snap", "run", "firefox"),
        ("firefox", "-P", "Personal"),
    ],
)
def test_login_uses_the_existing_browser_profile(tmp_path, monkeypatch, command):
    paths = paths_in(tmp_path)
    launched = []
    browser = Browser("default", "Browser", "firefox", command)
    monkeypatch.setattr(
        "riftlift.auth_browser.subprocess.Popen",
        lambda command, **options: launched.append((command, options)) or object(),
    )
    url = META_LOGIN_URL + "native_sso/confirm?native_sso_etoken=challenge"

    launch_browser_login(paths, browser, url)

    assert launched[0][0] == [*command, url]
    assert launched[0][1]["start_new_session"] is True
    assert not (paths.config / "auth").exists()


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


def test_runtime_access_token_returns_the_persisted_login(tmp_path: Path) -> None:
    paths = paths_in(tmp_path)
    token = "FRL" + "a" * 176
    paths.config.mkdir(parents=True)
    (paths.config / "meta-access-token").write_text(token + "\n")

    assert runtime_access_token(paths) == token


@pytest.mark.parametrize("returncode", [None, 0, 1])
@pytest.mark.parametrize("rejected", [False, True])
def test_cli_login_waits_for_callback_without_stopping_browser(
    tmp_path, monkeypatch, returncode, rejected
):
    paths = paths_in(tmp_path)
    browser = Browser("firefox", "Firefox", "firefox", ("firefox",))
    stopped = []
    process = SimpleNamespace(
        poll=lambda: returncode, terminate=lambda: stopped.append(True)
    )
    ready = iter([False, True])
    token = "FRL" + "a" * 176

    def complete():
        if rejected:
            raise RiftLiftError("rejected")
        return token

    session = SimpleNamespace(
        login_url=META_LOGIN_URL,
        callback_ready=lambda: next(ready),
        complete=complete,
    )
    monkeypatch.setattr("riftlift.auth.default_browser", lambda: browser)
    monkeypatch.setattr("riftlift.auth.MetaAuthSession.begin", lambda _paths: session)
    monkeypatch.setattr("riftlift.auth.launch_browser_login", lambda *_args: process)
    monkeypatch.setattr("riftlift.auth.time.sleep", lambda _: None)

    if returncode == 1 or rejected:
        message = "could not open" if returncode == 1 else "rejected"
        with pytest.raises(RiftLiftError, match=message):
            login(paths)
    else:
        assert login(paths) == 0
        assert runtime_access_token(paths) == token
    assert stopped == []


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
