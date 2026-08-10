from pathlib import Path

from riftlift.auth import (
    META_LOGIN_URL,
    browser_home,
    complete_browser_login,
    launch_browser_login,
    sign_out,
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


def test_edge_login_uses_an_isolated_riftlift_profile(
    tmp_path: Path, monkeypatch
) -> None:
    paths = paths_in(tmp_path)
    launched = []
    monkeypatch.setattr("riftlift.auth.browser_command", lambda _browser: ["edge"])
    monkeypatch.setattr(
        "riftlift.auth.subprocess.Popen",
        lambda command, **options: launched.append((command, options)) or object(),
    )

    launch_browser_login(paths, "edge")

    command, options = launched[0]
    assert command[0] == "edge"
    assert (
        f"--user-data-dir={browser_home(paths, 'edge') / '.config/microsoft-edge'}"
        in command
    )
    assert command[-1] == META_LOGIN_URL
    assert options["start_new_session"] is True


def test_firefox_login_uses_an_isolated_riftlift_profile(
    tmp_path: Path, monkeypatch
) -> None:
    paths = paths_in(tmp_path)
    launched = []
    monkeypatch.setattr("riftlift.auth.browser_command", lambda _browser: ["firefox"])
    monkeypatch.setattr(
        "riftlift.auth.subprocess.Popen",
        lambda command, **options: launched.append((command, options)) or object(),
    )

    launch_browser_login(paths, "firefox")

    command, _options = launched[0]
    assert command[:2] == ["firefox", "--no-remote"]
    assert str(browser_home(paths, "firefox")) in command[3]
    assert command[-1] == META_LOGIN_URL


def test_browser_login_imports_and_protects_the_token(
    tmp_path: Path, monkeypatch
) -> None:
    paths = paths_in(tmp_path)
    token = "FRL" + "a" * 176
    monkeypatch.setattr("riftlift.auth.get_access_token", lambda _home: token)

    assert complete_browser_login(paths, "firefox") == token
    target = paths.config / "meta-access-token"
    assert target.read_text().strip() == token
    assert target.stat().st_mode & 0o777 == 0o600


def test_sign_out_removes_only_riftlift_auth_state(tmp_path: Path) -> None:
    paths = paths_in(tmp_path)
    paths.create()
    token = paths.config / "meta-access-token"
    token.write_text("secret")
    cookie = browser_home(paths, "edge") / ".config/microsoft-edge/Cookies"
    cookie.parent.mkdir(parents=True)
    cookie.write_text("cookie")
    unrelated = paths.config / "settings.json"
    unrelated.write_text("keep")

    sign_out(paths)

    assert not token.exists()
    assert not (paths.config / "auth").exists()
    assert unrelated.read_text() == "keep"
