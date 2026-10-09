import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from riftlift import auth
from riftlift.auth import (
    account_tokens,
    accounts,
    complete_browser_login,
    login,
    owned_apps,
    prepare_login,
    record_owned,
    runtime_access_token,
    save_access_token,
    sign_out,
)
from riftlift.auth_browser import (
    META_LOGIN_URL,
    Browser,
    _desktop_browser,
    browser_home,
    default_browser,
    launch_browser_login,
)
from riftlift.config import Paths
from riftlift.entitlements import OwnedApp
from riftlift.util import RiftLiftError


@pytest.fixture(autouse=True)
def linux_browser_environment(monkeypatch):
    # Windows browser discovery and owned profiles have dedicated native tests.
    from riftlift import auth_browser

    monkeypatch.setattr(
        auth_browser, "os", SimpleNamespace(**{**vars(os), "name": "posix"})
    )


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

    assert complete_browser_login(paths, session).token == token
    target = paths.config / "meta-accounts.json"
    assert token in target.read_text()
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600


def test_first_login_keeps_browser_session_until_explicit_sign_out(
    tmp_path: Path,
) -> None:
    paths = paths_in(tmp_path)
    profile = paths.config / "auth/edge/profile"
    profile.mkdir(parents=True)
    (profile / "Preferences").write_text("{}")

    prepare_login(paths)

    assert (profile / "Preferences").exists()
    sign_out(paths)
    assert not profile.exists()


def test_adding_an_account_keeps_existing_accounts_but_resets_the_profile(
    tmp_path: Path,
) -> None:
    paths = paths_in(tmp_path)
    profile = paths.config / "auth/edge/profile"
    profile.mkdir(parents=True)
    save_access_token(paths, "FRL" + "a" * 176)

    prepare_login(paths)

    # Meta must ask which account to use, not reconfirm the remembered one.
    assert not profile.exists()
    assert [account.token for account in accounts(paths)] == ["FRL" + "a" * 176]


def test_accounts_are_added_replaced_and_signed_out_individually(
    tmp_path: Path,
) -> None:
    paths = paths_in(tmp_path)
    first = save_access_token(paths, "FRL" + "a" * 176, ("meta-1", "Alpha"))
    save_access_token(paths, "FRL" + "b" * 176, ("meta-2", "Beta"))
    # Signing the same Meta user in again refreshes its token in place.
    save_access_token(paths, "FRL" + "c" * 176, ("meta-1", ""))

    signed_in = accounts(paths)
    assert [(a.id, a.name, a.token[3]) for a in signed_in] == [
        ("meta-1", "Alpha", "c"),
        ("meta-2", "Beta", "b"),
    ]
    assert runtime_access_token(paths) == "FRL" + "c" * 176

    sign_out(paths, first.id)
    assert [account.id for account in accounts(paths)] == ["meta-2"]
    sign_out(paths, "meta-2")
    assert accounts(paths) == []
    assert not (paths.config / "meta-accounts.json").exists()


def test_legacy_single_token_becomes_the_first_account(tmp_path: Path) -> None:
    paths = paths_in(tmp_path)
    paths.config.mkdir(parents=True)
    (paths.config / "meta-access-token").write_text("FRL" + "a" * 176 + "\n")

    save_access_token(paths, "FRL" + "b" * 176, ("meta-2", "Beta"))

    assert [account.token[3] for account in accounts(paths)] == ["a", "b"]
    assert not (paths.config / "meta-access-token").exists()


def test_install_tokens_prefer_the_account_that_owns_the_app(tmp_path: Path) -> None:
    paths = paths_in(tmp_path)
    save_access_token(paths, "FRL" + "a" * 176, ("meta-1", "Alpha"))
    save_access_token(paths, "FRL" + "b" * 176, ("meta-2", "Beta"))
    record_owned(paths, "meta-2", ["42"])

    assert [token[3] for token in account_tokens(paths, "42")] == ["b", "a"]
    assert [token[3] for token in account_tokens(paths, "7")] == ["a", "b"]


def test_signed_out_install_tokens_explain_how_to_sign_in(tmp_path: Path) -> None:
    with pytest.raises(RiftLiftError, match="signed out"):
        account_tokens(paths_in(tmp_path))


def test_owned_apps_merges_accounts_and_reports_partial_failures(
    tmp_path: Path, monkeypatch
) -> None:
    paths = paths_in(tmp_path)
    save_access_token(paths, "FRL" + "a" * 176, ("meta-1", "Alpha"))
    save_access_token(paths, "FRL" + "b" * 176, ("meta-2", "Beta"))
    save_access_token(paths, "FRL" + "c" * 176, ("meta-3", ""))
    shared = OwnedApp("1", "Shared", "shared")
    libraries = {
        "a": [shared, OwnedApp("2", "Alpha only", "alpha")],
        "b": [OwnedApp("3", "beta only", "beta"), shared],
    }

    def list_owned(token):
        if token[3] not in libraries:
            raise RiftLiftError("expired")
        return libraries[token[3]]

    monkeypatch.setattr(auth.entitlements, "list_owned_pcvr_apps", list_owned)

    owned, failures = owned_apps(paths)

    assert [app.app_id for app in owned] == ["2", "3", "1"]
    assert failures == ["Meta account 3: expired"]
    assert {a.id: a.owned for a in accounts(paths)} == {
        "meta-1": ["1", "2"],
        "meta-2": ["1", "3"],
        "meta-3": [],
    }

    libraries.clear()
    with pytest.raises(RiftLiftError, match="expired"):
        owned_apps(paths)


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

    save_access_token(paths, "FRL" + "a" * 176)

    sign_out(paths)

    assert not token.exists()
    assert not (paths.config / "meta-accounts.json").exists()
    assert not (paths.config / "auth").exists()
    assert unrelated.read_text() == "keep"
