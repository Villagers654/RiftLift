"""Fetch the Meta account's owned Rift/PC VR entitlements."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .util import RiftLiftError, read_limited

GRAPHQL_URL = "https://graph.oculus.com/graphql"
ENTITLEMENTS_DOCUMENT_ID = "4850747515044496"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class OwnedApp:
    app_id: str
    name: str
    slug: str

    @property
    def store_url(self) -> str:
        return f"https://www.meta.com/experiences/pcvr/{self.slug}/{self.app_id}/"


def list_owned_pcvr_apps(token: str) -> list[OwnedApp]:
    body = urllib.parse.urlencode(
        {
            "access_token": token,
            "variables": json.dumps({}),
            "doc_id": ENTITLEMENTS_DOCUMENT_ID,
        }
    ).encode()
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(
                read_limited(response, _MAX_RESPONSE_BYTES, "Meta entitlements")
            )
    except (OSError, TimeoutError) as error:
        raise RiftLiftError(f"could not reach the Meta API: {error}") from error
    if errors := payload.get("errors"):
        raise RiftLiftError(f"Meta API error: {errors[0].get('message', errors[0])}")
    try:
        entitlements = payload["data"]["viewer"]["user"]["active_entitlements"]
        nodes = entitlements["nodes"]
    except (KeyError, TypeError) as error:
        raise RiftLiftError("Meta returned no entitlement data") from error
    print(
        f"Meta entitlements: {len(nodes)} node(s) returned"
        f"{'; a page_info field is present (results may be paginated and truncated)' if 'page_info' in entitlements else ''}."
    )
    apps = []
    skipped_platforms: dict[str, int] = {}
    skipped_incomplete = 0
    for node in nodes:
        item = node.get("item") or {}
        platform = item.get("platform")
        if platform != "PC":
            skipped_platforms[str(platform)] = (
                skipped_platforms.get(str(platform), 0) + 1
            )
            continue
        app_id, name = item.get("id"), item.get("display_name")
        slug = item.get("canonical_name")
        if app_id and name and slug:
            apps.append(OwnedApp(app_id=str(app_id), name=str(name), slug=str(slug)))
        else:
            skipped_incomplete += 1
    if skipped_platforms or skipped_incomplete:
        print(
            f"Meta entitlements: skipped {skipped_incomplete} incomplete node(s); "
            f"skipped by platform: {skipped_platforms}"
        )
    return sorted(apps, key=lambda app: app.name.lower())
