"""Modern Meta native-SSO protocol used by RiftLift authentication."""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from .config import Paths
from .util import RiftLiftError, atomic_write_text, run

FRL_APP_ID = "512466987071624"
OCULUS_APP_ID = "1582076955407037"
FRL_CLIENT_TOKEN = f"FRL|{FRL_APP_ID}|01d4a1f7fd0682aea7ee8ae987704d63"
META_GRAPH = "https://meta.graph.meta.com"
META_AUTH_URL = "https://auth.meta.com/native_sso/confirm"
PROFILE_TOKEN_DOCUMENT = "24112177345042346"


def _multipart(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = f"----RiftLift{secrets.token_hex(16)}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), boundary


def _post(path: str, fields: dict[str, str]) -> dict:
    body, boundary = _multipart(fields)
    request = Request(
        f"{META_GRAPH}{path}",
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
    except HTTPError as error:
        try:
            payload = json.load(error)
            message = payload.get("error", {}).get("message")
        except (AttributeError, json.JSONDecodeError, UnicodeError):
            message = None
        raise RiftLiftError(
            message or "Meta rejected the authentication request"
        ) from error
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeError) as error:
        raise RiftLiftError("Could not reach Meta's authentication service") from error
    if not isinstance(result, dict):
        raise RiftLiftError("Meta returned an invalid authentication response")
    if error := result.get("error"):
        message = error.get("message") if isinstance(error, dict) else None
        raise RiftLiftError(message or "Meta rejected the authentication request")
    return result


def _callback_file(paths: Paths) -> Path:
    return paths.config / "meta-auth-callback"


def record_callback(paths: Paths, callback_url: str) -> int:
    """Safely hand a browser's custom-scheme callback to the active GUI/CLI."""
    parsed = urlsplit(callback_url)
    if parsed.scheme not in {"oculus", "oculus-client"}:
        raise RiftLiftError("Meta login callback must use the oculus:// scheme")
    paths.create()
    target = _callback_file(paths)
    atomic_write_text(target, callback_url)
    return 0


def install_protocol_handler() -> Path:
    """Register RiftLift as the host handler for Meta's browser callback."""
    applications = Path.home() / ".local/share/applications"
    applications.mkdir(parents=True, exist_ok=True)
    desktop = applications / "riftlift-meta-login.desktop"
    executable = Path.home() / ".local/bin/riftlift"
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=RiftLift Meta Login\n"
        "NoDisplay=true\n"
        f"Exec={executable} callback %u\n"
        "MimeType=x-scheme-handler/oculus;x-scheme-handler/oculus-client;\n"
    )
    desktop.chmod(0o755)
    if update_database := shutil.which("update-desktop-database"):
        run((update_database, str(applications)))
    if xdg_mime := shutil.which("xdg-mime"):
        run((xdg_mime, "default", desktop.name, "x-scheme-handler/oculus"))
        run((xdg_mime, "default", desktop.name, "x-scheme-handler/oculus-client"))
    return desktop


@dataclass(slots=True)
class MetaAuthSession:
    """One short-lived native-SSO challenge and its verified callback."""

    paths: Paths
    request_token: str
    login_url: str

    @classmethod
    def begin(cls, paths: Paths) -> MetaAuthSession:
        install_protocol_handler()
        _callback_file(paths).unlink(missing_ok=True)
        response = _post("/webview_tokens_query", {"access_token": FRL_CLIENT_TOKEN})
        request_token = response.get("native_sso_token")
        etoken = response.get("native_sso_etoken")
        if not isinstance(request_token, str) or not isinstance(etoken, str):
            raise RiftLiftError("Meta did not create a login challenge")
        query = urlencode(
            {
                "native_app_id": FRL_APP_ID,
                "source_app_id": FRL_APP_ID,
                "native_sso_etoken": etoken,
                "utm_source": "riftlift",
            }
        )
        return cls(paths, request_token, f"{META_AUTH_URL}?{query}")

    def callback_ready(self) -> bool:
        return _callback_file(self.paths).is_file()

    def complete(self) -> str:
        target = _callback_file(self.paths)
        try:
            callback_url = target.read_text()
        except FileNotFoundError as error:
            raise RiftLiftError("Meta has not finished authentication yet") from error
        finally:
            target.unlink(missing_ok=True)
        parsed = urlsplit(callback_url)
        query = parse_qs(parsed.query)
        blob = query.get("blob", [""])[0]
        callback_hash = query.get("token", [""])[0]
        expected_hash = hashlib.sha256(self.request_token.encode()).hexdigest()[:16]
        if parsed.scheme not in {
            "oculus",
            "oculus-client",
        } or not secrets.compare_digest(callback_hash, expected_hash):
            raise RiftLiftError("Meta returned an invalid login callback")
        if not blob:
            raise RiftLiftError("Meta's login callback did not contain a session")

        decrypted = _post(
            "/webview_blobs_decrypt",
            {
                "access_token": FRL_CLIENT_TOKEN,
                "blob": blob,
                "request_token": self.request_token,
            },
        )
        meta_token = decrypted.get("access_token")
        if not isinstance(meta_token, str):
            raise RiftLiftError("Meta did not return an account token")
        response = _post(
            "/graphql",
            {
                "access_token": meta_token,
                "doc_id": PROFILE_TOKEN_DOCUMENT,
                "variables": json.dumps(
                    {"app_id": OCULUS_APP_ID}, separators=(",", ":")
                ),
            },
        )
        try:
            token = response["data"]["xfr_create_profile_token"]["profile_tokens"][0][
                "access_token"
            ]
        except (KeyError, IndexError, TypeError) as error:
            raise RiftLiftError(
                "Meta did not return an Oculus profile token"
            ) from error
        if not isinstance(token, str):
            raise RiftLiftError("Meta returned an invalid Oculus profile token")
        return token


def clear_callback(paths: Paths) -> None:
    _callback_file(paths).unlink(missing_ok=True)
