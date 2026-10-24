import io
import json

from riftlift import entitlements
from riftlift.entitlements import OwnedApp, list_owned_pcvr_apps


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _serve(monkeypatch, nodes, page_info=None) -> None:
    connection = {"nodes": nodes}
    if page_info is not None:
        connection["page_info"] = page_info
    payload = {"data": {"viewer": {"user": {"active_entitlements": connection}}}}
    monkeypatch.setattr(
        entitlements.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(json.dumps(payload).encode()),
    )


def _item(app_id, name, slug, platform="PC"):
    return {
        "item": {
            "id": app_id,
            "display_name": name,
            "canonical_name": slug,
            "platform": platform,
        }
    }


def test_owned_apps_without_a_canonical_name_are_kept(monkeypatch) -> None:
    _serve(
        monkeypatch,
        [_item("111", "Echo VR", None), _item("222", "Lone Echo", "lone-echo")],
    )

    owned = list_owned_pcvr_apps("token")

    assert [app.name for app in owned] == ["Echo VR", "Lone Echo"]
    assert owned[0].store_url == "https://www.meta.com/experiences/pcvr/111/"
    assert owned[1].store_url == (
        "https://www.meta.com/experiences/pcvr/lone-echo/222/"
    )


def test_owned_apps_skip_other_platforms_and_nameless_items(monkeypatch) -> None:
    _serve(
        monkeypatch,
        [
            _item("1", "Quest Game", "quest", platform="ANDROID_6DOF"),
            _item("2", None, "nameless"),
            _item("3", "Stormland", "stormland"),
        ],
    )

    assert list_owned_pcvr_apps("token") == [
        OwnedApp(app_id="3", name="Stormland", slug="stormland")
    ]


def test_owned_apps_report_a_list_meta_cut_short(monkeypatch) -> None:
    _serve(
        monkeypatch,
        [_item("1", "Stormland", "stormland")],
        page_info={"has_next_page": True, "end_cursor": "abc"},
    )

    assert list_owned_pcvr_apps("token").partial is True


def test_owned_apps_are_complete_without_a_next_page(monkeypatch) -> None:
    _serve(
        monkeypatch,
        [_item("1", "Stormland", "stormland")],
        page_info={"has_next_page": False},
    )
    assert list_owned_pcvr_apps("token").partial is False

    _serve(monkeypatch, [_item("1", "Stormland", "stormland")])
    assert list_owned_pcvr_apps("token").partial is False
