"""Meta account persistence for RiftLift's native-SSO flow.

RiftLift can hold several Meta accounts at once. Each keeps its own Oculus
profile token and the app IDs it was last seen to own, so an install can use
the token of the account that actually owns the game.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field

from . import entitlements
from .auth_browser import (
    cleanup_browser_profiles,
    default_browser,
    launch_browser_login,
    stop_browser,
)
from .config import Paths
from .meta_auth import MetaAuthSession, clear_callback, record_callback
from .util import RiftLiftError, atomic_write_text

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_.|-]{32,4096}")
_ACCOUNTS_FILE = "meta-accounts.json"
_LEGACY_TOKEN_FILE = "meta-access-token"


@dataclass(slots=True)
class Account:
    """One signed-in Meta account."""

    id: str
    token: str
    name: str = ""
    owned: list[str] = field(default_factory=list)


def _valid_token(value: object) -> bool:
    return isinstance(value, str) and _TOKEN_PATTERN.fullmatch(value) is not None


def _fallback_id(token: str) -> str:
    return "token-" + hashlib.sha256(token.encode()).hexdigest()[:16]


def _read_accounts_file(paths: Paths) -> list[Account] | None:
    try:
        value = json.loads((paths.config / _ACCOUNTS_FILE).read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    entries = value.get("accounts") if isinstance(value, dict) else None
    result = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not _valid_token(entry.get("token")):
            continue
        owned = entry.get("owned")
        result.append(
            Account(
                id=str(entry.get("id") or _fallback_id(entry["token"])),
                token=entry["token"],
                name=str(entry.get("name") or ""),
                owned=[str(item) for item in owned] if isinstance(owned, list) else [],
            )
        )
    return result


def _write_accounts(paths: Paths, values: list[Account]) -> None:
    paths.create()
    atomic_write_text(
        paths.config / _ACCOUNTS_FILE,
        json.dumps({"accounts": [asdict(item) for item in values]}, indent=2) + "\n",
    )


def accounts(paths: Paths) -> list[Account]:
    """Return every signed-in Meta account, oldest first.

    A token saved by an older single-account RiftLift is adopted as the first
    account the first time it is read.
    """
    stored = _read_accounts_file(paths)
    legacy = paths.config / _LEGACY_TOKEN_FILE
    try:
        legacy_token = legacy.read_text().strip()
    except (FileNotFoundError, OSError, UnicodeError):
        return stored or []
    if _valid_token(legacy_token) and not any(
        item.token == legacy_token for item in stored or []
    ):
        stored = [Account(_fallback_id(legacy_token), legacy_token), *(stored or [])]
        _write_accounts(paths, stored)
    legacy.unlink(missing_ok=True)
    return stored or []


def complete_browser_login(paths: Paths, session: MetaAuthSession) -> Account:
    """Finish Meta native SSO and add (or refresh) the resulting account."""
    token = session.complete()
    return save_access_token(paths, token, entitlements.fetch_account_identity(token))


def complete_login(paths: Paths, callback_url: str) -> int:
    """Hand a browser's custom-scheme callback to the active auth session."""
    return record_callback(paths, callback_url)


def login(paths: Paths) -> int:
    """Run the browser-backed sign-in flow for command-line users."""
    browser = default_browser()
    prepare_login(paths)
    session = MetaAuthSession.begin(paths)
    process = launch_browser_login(paths, browser, session.login_url)
    print(f"Finish signing in to Meta in {browser.name}.")
    if accounts(paths):
        print("If Meta offers an account you already added, switch accounts there.")
    try:
        while True:
            if session.callback_ready():
                account = complete_browser_login(paths, session)
                label = f" as {account.name}" if account.name else ""
                print(f"RiftLift is signed in to Meta{label}.")
                return 0
            if process is not None and process.poll() not in (None, 0):
                raise RiftLiftError("could not open the browser for Meta sign-in")
            time.sleep(1)
    finally:
        stop_browser(paths, browser, process)


def prepare_login(paths: Paths) -> None:
    """Start a new login without signing any existing account out.

    When an account is already signed in, RiftLift's own isolated browser
    profile is reset so Meta asks which account to use instead of silently
    confirming the one it remembers.
    """
    clear_callback(paths)
    if accounts(paths):
        cleanup_browser_profiles(paths)


def sign_out(paths: Paths, account_id: str | None = None) -> None:
    """Forget one account, or every account and the isolated login profiles."""
    if account_id is not None:
        remaining = [item for item in accounts(paths) if item.id != account_id]
        if remaining:
            _write_accounts(paths, remaining)
            return
    (paths.config / _ACCOUNTS_FILE).unlink(missing_ok=True)
    (paths.config / _LEGACY_TOKEN_FILE).unlink(missing_ok=True)
    clear_callback(paths)
    cleanup_browser_profiles(paths)


def save_access_token(
    paths: Paths, token: str, identity: tuple[str, str] | None = None
) -> Account:
    """Add an account, or replace the token of the same account signing in again.

    ``identity`` is Meta's (user ID, display name) when it could be looked up;
    without it the account is keyed by its token.
    """
    if not _valid_token(token):
        raise RiftLiftError("Meta returned an invalid Oculus profile token")
    account_id, name = identity or (_fallback_id(token), "")
    existing = accounts(paths)
    previous = next(
        (item for item in existing if item.id == account_id or item.token == token),
        None,
    )
    account = Account(
        account_id,
        token,
        name or (previous.name if previous else ""),
        previous.owned if previous else [],
    )
    updated = [account if item is previous else item for item in existing]
    if previous is None:
        updated.append(account)
    _write_accounts(paths, updated)
    return account


def record_owned(paths: Paths, account_id: str, app_ids: list[str]) -> None:
    """Remember which apps an account owns so installs can pick its token."""
    current = accounts(paths)
    for item in current:
        if item.id == account_id:
            item.owned = sorted(set(app_ids))
    _write_accounts(paths, current)


def account_tokens(paths: Paths, app_id: str | None = None) -> list[str]:
    """Return every account token, those known to own ``app_id`` first."""
    signed_in = accounts(paths)
    if not signed_in:
        raise RiftLiftError(
            "RiftLift is signed out. Open Sign In and finish Meta authentication."
        )
    owners = [item for item in signed_in if app_id and app_id in item.owned]
    return [item.token for item in owners + [a for a in signed_in if a not in owners]]


def runtime_access_token(paths: Paths) -> str:
    """Return the first signed-in account's Meta token."""
    return account_tokens(paths)[0]


def owned_apps(paths: Paths) -> tuple[entitlements.OwnedLibrary, list[str]]:
    """List Rift/PC VR apps owned by any signed-in account.

    Returns the merged apps and one message per account that could not be
    read; raises only when no account could be read at all. The list is
    marked partial when Meta cut any account's list short.
    """
    merged: dict[str, entitlements.OwnedApp] = {}
    failures = []
    partial = False
    signed_in = accounts(paths)
    if not signed_in:
        account_tokens(paths)  # raises the signed-out error
    for number, account in enumerate(signed_in, 1):
        try:
            owned = entitlements.list_owned_pcvr_apps(account.token)
        except Exception as error:
            failures.append(f"{account.name or f'Meta account {number}'}: {error}")
            continue
        record_owned(paths, account.id, [app.app_id for app in owned])
        partial = partial or getattr(owned, "partial", False)
        for app in owned:
            merged.setdefault(app.app_id, app)
    if failures and len(failures) == len(signed_in):
        raise RiftLiftError(failures[0] if len(failures) == 1 else "; ".join(failures))
    apps = sorted(merged.values(), key=lambda app: app.name.lower())
    return entitlements.OwnedLibrary(apps, partial=partial), failures
