"""List every Rift/PCVR build a Meta account can download.

Meta's ``supportedBinaries`` query, used by ``meta_pcvr_downloader.list_builds``,
is a persisted query fixed at the newest 20 builds. The app-history query used
here (the same data OculusDB shows) returns every binary ever uploaded. Only
binaries that were published to at least one release channel can be fetched
from Meta's CDN; the others are internal uploads and return HTTP 404.
"""

from __future__ import annotations

from dataclasses import dataclass

from meta_pcvr_downloader.api import (
    Build,
    MetaApiError,
    _post_graphql,
    list_builds,
)

HISTORY_DOCUMENT_ID = "2885322071572384"


@dataclass(frozen=True)
class AvailableBuild(Build):
    """A downloadable build plus the release channels it was published to.

    ``offered`` marks the builds Meta's ``supportedBinaries`` query returns,
    the ones Meta offers this account by default.
    """

    channels: tuple[str, ...] = ()
    offered: bool = False


def parse_history(payload: dict, app_id: str, app_name: str) -> list[AvailableBuild]:
    """Return the published PC binaries from an app-history GraphQL response."""
    try:
        nodes = payload["data"]["node"]["primary_binaries"]["nodes"]
    except (KeyError, TypeError) as error:
        raise MetaApiError("Meta returned no build history for that app") from error
    builds = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("__typename") != "PCBinary":
            continue
        channel_nodes = (node.get("binary_release_channels") or {}).get("nodes") or []
        channels = tuple(
            str(channel["channel_name"])
            for channel in channel_nodes
            if isinstance(channel, dict) and channel.get("channel_name")
        )
        if not channels or not node.get("id"):
            continue
        builds.append(
            AvailableBuild(
                app_id=app_id,
                app_name=app_name,
                binary_id=str(node["id"]),
                version=str(node.get("version") or "unknown"),
                version_code=int(node.get("version_code") or 0),
                channels=channels,
            )
        )
    return builds


def merge_builds(
    primary: list[Build], history: list[AvailableBuild]
) -> list[AvailableBuild]:
    """Merge both build lists by binary ID, newest version code first."""
    merged: dict[str, AvailableBuild] = {build.binary_id: build for build in history}
    for build in primary:
        known = merged.get(build.binary_id)
        merged[build.binary_id] = AvailableBuild(
            app_id=build.app_id,
            app_name=build.app_name,
            binary_id=build.binary_id,
            version=build.version,
            version_code=build.version_code,
            change_log=build.change_log,
            channels=known.channels if known else (),
            offered=True,
        )
    return sorted(merged.values(), key=lambda build: build.version_code, reverse=True)


def default_build(builds: list[Build]) -> Build:
    """Return the build a plain install picks: the newest one Meta offers.

    The full history can hold newer builds that are only on an alpha or beta
    channel. They stay installable when chosen, but never replace the default.
    """
    return next(
        (build for build in builds if getattr(build, "offered", True)), builds[0]
    )


def list_all_builds(token: str, app_id: str) -> list[AvailableBuild]:
    """Return every build this account can download, newest first.

    The newest-20 list is always fetched because it carries the app name and
    change logs. The full history is best effort: if Meta retires or changes
    that query, RiftLift falls back to the newest 20 builds with a warning.
    """
    primary = list_builds(token, app_id)
    try:
        payload = _post_graphql(token, HISTORY_DOCUMENT_ID, {"applicationID": app_id})
        history = parse_history(payload, app_id, primary[0].app_name)
    except MetaApiError as error:
        print(f"warning: could not read the full build history: {error}")
        history = []
    return merge_builds(primary, history)


def builds_table(builds: list[AvailableBuild]) -> list[str]:
    """Lines listing every build, as ``riftlift builds`` prints them."""
    lines = [
        f"{builds[0].app_name}: {len(builds)} downloadable version(s)",
        f"{'VERSION':<24} {'CODE':>6}  {'BINARY ID':<20} CHANNELS",
    ]
    for build in builds:
        channels = ", ".join(build.channels) or "-"
        lines.append(
            f"{build.version:<24} {build.version_code:>6}  "
            f"{build.binary_id:<20} {channels}"
        )
    return lines


def build_label(build: Build) -> str:
    """Return a short human label, such as ``34.4.631547.1 (2202)``."""
    return f"{build.version} ({build.version_code})"
