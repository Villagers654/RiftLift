import hashlib
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from riftlift.config import Paths
from riftlift.meta_auth import (
    FRL_APP_ID,
    META_AUTH_URL,
    MetaAuthSession,
    install_protocol_handler,
    record_callback,
)
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


def test_session_creates_a_native_sso_challenge(tmp_path, monkeypatch) -> None:
    paths = paths_in(tmp_path)
    monkeypatch.setattr("riftlift.meta_auth.install_protocol_handler", lambda: None)
    monkeypatch.setattr(
        "riftlift.meta_auth._post",
        lambda path, _fields: {
            "native_sso_token": "request-token",
            "native_sso_etoken": "encrypted-token",
        },
    )

    session = MetaAuthSession.begin(paths)
    parsed = urlsplit(session.login_url)
    query = parse_qs(parsed.query)

    assert session.request_token == "request-token"
    assert session.login_url.startswith(META_AUTH_URL)
    assert query["native_app_id"] == [FRL_APP_ID]
    assert query["native_sso_etoken"] == ["encrypted-token"]


def test_protocol_handler_uses_the_installed_command(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "bin with spaces/riftlift"
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_BIN_HOME", str(executable.parent))
    monkeypatch.setattr("riftlift.meta_auth.shutil.which", lambda _name: None)
    monkeypatch.setattr("riftlift.meta_auth.run", lambda _arguments, **_kwargs: None)

    desktop = install_protocol_handler()

    assert f'Exec="{executable}" callback %u' in desktop.read_text()
    assert desktop.stat().st_mode & 0o777 == 0o644


def test_protocol_handler_honors_xdg_data_home(tmp_path, monkeypatch) -> None:
    data_home = tmp_path / "share"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setattr("riftlift.meta_auth.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "riftlift.meta_auth.installed_command", lambda _name: tmp_path / "riftlift"
    )

    desktop = install_protocol_handler()

    assert desktop == data_home / "applications/riftlift-meta-login.desktop"


def test_protocol_handler_uses_url_scheme_registration(tmp_path, monkeypatch) -> None:
    calls = []
    executable = tmp_path / "riftlift"
    executable.touch()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(
        "riftlift.meta_auth.installed_command", lambda _name: executable
    )
    monkeypatch.setattr(
        "riftlift.meta_auth.shutil.which",
        lambda name: "/usr/bin/xdg-settings" if name == "xdg-settings" else None,
    )
    monkeypatch.setattr(
        "riftlift.meta_auth.run",
        lambda arguments, **kwargs: calls.append((arguments, kwargs)),
    )

    install_protocol_handler()

    assert calls == [
        (
            (
                "/usr/bin/xdg-settings",
                "set",
                "default-url-scheme-handler",
                "oculus",
                "riftlift-meta-login.desktop",
            ),
            {"timeout": 10},
        ),
        (
            (
                "/usr/bin/xdg-settings",
                "set",
                "default-url-scheme-handler",
                "oculus-client",
                "riftlift-meta-login.desktop",
            ),
            {"timeout": 10},
        ),
    ]


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.CalledProcessError(2, "xdg-settings"),
        subprocess.TimeoutExpired("xdg-settings", 10),
        OSError("could not execute xdg-settings"),
    ],
)
def test_protocol_handler_falls_back_to_mime_registration(
    tmp_path, monkeypatch, failure
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(
        "riftlift.meta_auth.installed_command",
        lambda _name: tmp_path / "path with spaces/riftlift",
    )
    monkeypatch.setattr(
        "riftlift.meta_auth.shutil.which",
        lambda name: (
            f"/usr/bin/{name}" if name in {"xdg-settings", "xdg-mime"} else None
        ),
    )
    calls = []

    def register(arguments, **_kwargs):
        calls.append(arguments)
        if arguments[0].endswith("xdg-settings"):
            raise failure

    monkeypatch.setattr("riftlift.meta_auth.run", register)
    desktop = install_protocol_handler()

    assert (
        f'Exec="{tmp_path}/path with spaces/riftlift" callback %u'
        in desktop.read_text()
    )
    assert [call for call in calls if call[0].endswith("xdg-mime")] == [
        ("/usr/bin/xdg-mime", "default", desktop.name, f"x-scheme-handler/{scheme}")
        for scheme in ("oculus", "oculus-client")
    ]


def test_protocol_handler_reports_failed_mime_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(
        "riftlift.meta_auth.installed_command", lambda _name: tmp_path / "riftlift"
    )
    monkeypatch.setattr(
        "riftlift.meta_auth.shutil.which", lambda name: f"/usr/bin/{name}"
    )

    def fail(arguments, **_kwargs):
        if arguments[0].endswith(("xdg-settings", "xdg-mime")):
            raise subprocess.CalledProcessError(2, arguments)

    monkeypatch.setattr("riftlift.meta_auth.run", fail)
    with pytest.raises(RiftLiftError, match="could not register"):
        install_protocol_handler()


def test_verified_callback_is_exchanged_for_oculus_token(tmp_path, monkeypatch) -> None:
    paths = paths_in(tmp_path)
    request_token = "request-token"
    callback_hash = hashlib.sha256(request_token.encode()).hexdigest()[:16]
    record_callback(paths, f"oculus://login?blob=opaque&token={callback_hash}")
    calls = []

    def post(path, fields):
        calls.append((path, fields))
        if path == "/webview_blobs_decrypt":
            return {"access_token": "meta-token"}
        return {
            "data": {
                "xfr_create_profile_token": {
                    "profile_tokens": [{"access_token": "OC" + "x" * 64}]
                }
            }
        }

    monkeypatch.setattr("riftlift.meta_auth._post", post)
    session = MetaAuthSession(paths, request_token, "https://example.com")

    assert session.complete() == "OC" + "x" * 64
    assert calls[0][0] == "/webview_blobs_decrypt"
    assert calls[1][0] == "/graphql"
    assert not session.callback_ready()


def test_callback_must_match_the_active_challenge(tmp_path) -> None:
    paths = paths_in(tmp_path)
    record_callback(paths, "oculus://login?blob=opaque&token=wrong")
    session = MetaAuthSession(paths, "request-token", "https://example.com")

    with pytest.raises(RiftLiftError, match="invalid login callback"):
        session.complete()


def test_callback_rejects_untrusted_schemes(tmp_path) -> None:
    with pytest.raises(RiftLiftError, match="oculus"):
        record_callback(paths_in(tmp_path), "https://example.com/callback")


def test_callback_has_a_bounded_size(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("riftlift.meta_auth._MAX_CALLBACK_BYTES", 32)

    with pytest.raises(RiftLiftError, match="2 MiB limit"):
        record_callback(paths_in(tmp_path), "oculus://login?blob=" + "x" * 32)
