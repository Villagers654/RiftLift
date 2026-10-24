from meta_pcvr_downloader.api import Build, MetaApiError

from riftlift import builds as builds_module
from riftlift.builds import AvailableBuild, merge_builds, parse_history


def _binary(binary_id, version, code, channels, typename="PCBinary"):
    return {
        "__typename": typename,
        "id": binary_id,
        "version": version,
        "version_code": code,
        "binary_release_channels": {"nodes": [{"channel_name": c} for c in channels]},
    }


HISTORY = {
    "data": {
        "node": {
            "primary_binaries": {
                "nodes": [
                    _binary("3", "3.0", 3, ["LIVE"]),
                    _binary("2", "2.0-internal", 2, []),
                    _binary("1", "1.0", 1, ["LIVE", "legacy"]),
                    _binary("9", "9.0", 9, ["LIVE"], typename="AndroidBinary"),
                ]
            }
        }
    }
}


def test_history_keeps_only_published_pc_binaries() -> None:
    result = parse_history(HISTORY, "42", "Game")

    assert [build.binary_id for build in result] == ["3", "1"]
    assert result[1].channels == ("LIVE", "legacy")
    assert result[0].app_name == "Game"


def test_merge_prefers_primary_details_and_keeps_channels() -> None:
    primary = [Build("42", "Game", "3", "3.0", 3, change_log="notes")]
    history = parse_history(HISTORY, "42", "Game")

    merged = merge_builds(primary, history)

    assert [build.binary_id for build in merged] == ["3", "1"]
    assert merged[0].change_log == "notes"
    assert merged[0].channels == ("LIVE",)


def test_history_failure_falls_back_to_newest_builds(monkeypatch, capsys) -> None:
    primary = [Build("42", "Game", "3", "3.0", 3)]
    monkeypatch.setattr(builds_module, "list_builds", lambda _token, _app: primary)

    def fail(*_args):
        raise MetaApiError("Meta API error: document retired")

    monkeypatch.setattr(builds_module, "_post_graphql", fail)

    result = builds_module.list_all_builds("token", "42")

    assert [build.binary_id for build in result] == ["3"]
    assert isinstance(result[0], AvailableBuild)
    assert "could not read the full build history" in capsys.readouterr().out
