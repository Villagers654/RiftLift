import os
import threading
import time
from pathlib import Path

if os.name != "nt":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from riftlift import auth
from riftlift.auth import owned_apps, save_access_token
from riftlift.config import Paths
from riftlift.entitlements import OwnedApp, OwnedLibrary
from riftlift.main_window import Window
from riftlift.native_shell import search_words

ICON = str(Path(__file__).parents[1] / "src/riftlift/assets/mark.svg")


def _wait_until(app, condition, timeout: float = 2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def _paths(tmp_path: Path) -> Paths:
    paths = Paths(
        *(
            tmp_path / name
            for name in ("data", "cache", "config", "games", "prefix", "tools")
        )
    )
    paths.create()
    return paths


def _owned_window(tmp_path, monkeypatch, owned, fetch_icon):
    monkeypatch.setattr("riftlift.main_window.owned_apps", lambda _paths: (owned, []))
    monkeypatch.setattr("riftlift.main_window.fetch_owned_icon", fetch_icon)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = Window(_paths(tmp_path), start_services=False)
    window.refresh_owned()
    return app, window


def _visible_titles(window) -> list[str]:
    category = window._owned_category
    return [
        category.child(index).text(0)
        for index in range(category.childCount())
        if not category.child(index).isHidden()
    ]


def test_owned_games_show_before_their_icons_arrive(tmp_path, monkeypatch) -> None:
    release = threading.Event()

    def slow_icon(_paths, _app_id):
        release.wait(5)
        return ICON

    owned = [
        OwnedApp("1", "Lone Echo", "lone-echo"),
        OwnedApp("2", "Stormland", "stormland"),
    ]
    app, window = _owned_window(tmp_path, monkeypatch, owned, slow_icon)

    assert _wait_until(app, lambda: window._owned_category.childCount() == 2)
    placeholders = [
        window._owned_category.child(index).icon(0).cacheKey() for index in range(2)
    ]
    assert window._owned_icons == {}

    release.set()
    assert _wait_until(app, lambda: set(window._owned_icons) == {"1", "2"})
    assert all(
        window._owned_category.child(index).icon(0).cacheKey() != placeholders[index]
        for index in range(2)
    )
    window.close()
    app.processEvents()


def test_a_failed_icon_keeps_the_owned_game_listed(tmp_path, monkeypatch) -> None:
    def broken_icon(_paths, app_id):
        if app_id == "1":
            raise OSError("no catalog page")
        return ICON

    owned = [OwnedApp("1", "Echo VR", ""), OwnedApp("2", "Stormland", "stormland")]
    app, window = _owned_window(tmp_path, monkeypatch, owned, broken_icon)

    assert _wait_until(app, lambda: "2" in window._owned_icons)
    assert _visible_titles(window) == ["Echo VR", "Stormland"]
    assert "1" not in window._owned_icons
    window.close()
    app.processEvents()


def test_a_partial_owned_list_says_so(tmp_path, monkeypatch) -> None:
    owned = OwnedLibrary([OwnedApp("1", "Stormland", "stormland")], partial=True)
    app, window = _owned_window(tmp_path, monkeypatch, owned, lambda *_: None)

    assert _wait_until(app, lambda: window.owned)
    assert not window.owned_hint.isHidden()
    assert "Add Game" in window.owned_hint.text()

    window.owned = []
    monkeypatch.setattr(
        "riftlift.main_window.owned_apps",
        lambda _paths: (OwnedLibrary([OwnedApp("1", "Stormland", "stormland")]), []),
    )
    window.refresh_owned()
    assert _wait_until(app, lambda: window.owned_hint.isHidden())
    window.close()
    app.processEvents()


def test_owned_apps_stays_partial_when_any_account_was_cut_short(
    tmp_path, monkeypatch
) -> None:
    paths = _paths(tmp_path)
    save_access_token(paths, "FRL" + "a" * 176, ("meta-1", "Alpha"))
    save_access_token(paths, "FRL" + "b" * 176, ("meta-2", "Beta"))
    libraries = {
        "a": OwnedLibrary([OwnedApp("1", "Lone Echo", "lone-echo")]),
        "b": OwnedLibrary([OwnedApp("2", "Stormland", "stormland")], partial=True),
    }
    monkeypatch.setattr(
        auth.entitlements, "list_owned_pcvr_apps", lambda token: libraries[token[3]]
    )

    owned, failures = owned_apps(paths)
    assert [app.name for app in owned] == ["Lone Echo", "Stormland"]
    assert owned.partial and failures == []

    libraries["b"].partial = False
    assert not owned_apps(paths)[0].partial


def test_library_search_ignores_word_order_accents_and_punctuation(
    tmp_path, monkeypatch
) -> None:
    owned = [
        OwnedApp("1", "Lone Echo", "lone-echo"),
        OwnedApp("2", "Vader Immortal: Episode I", "vader-immortal-episode-i"),
        OwnedApp("3", "Pokémon Café", "pokemon-cafe"),
        OwnedApp("4", "Stormland", "stormland"),
    ]
    app, window = _owned_window(tmp_path, monkeypatch, owned, lambda *_: None)
    assert _wait_until(app, lambda: window._owned_category.childCount() == 4)

    for query, expected in (
        ("echo lone", ["Lone Echo"]),
        ("vader episode", ["Vader Immortal: Episode I"]),
        ("immortal:", ["Vader Immortal: Episode I"]),
        ("pokemon cafe", ["Pokémon Café"]),
        ("storm", ["Stormland"]),
        ("", ["Lone Echo", "Vader Immortal: Episode I", "Pokémon Café", "Stormland"]),
    ):
        window.search.setText(query)
        assert _visible_titles(window) == expected, query
    window.search.setText("nothing like this")
    assert window.search_empty.isVisibleTo(window)
    window.close()
    app.processEvents()


def test_search_words_normalise_case_accents_and_punctuation() -> None:
    assert search_words("  Pokémon: CAFÉ-Racer ") == ["pokemon", "cafe", "racer"]
    assert search_words("Lucky\u2019s Tale") == search_words("lucky's tale")
    assert search_words("Lucky\u2019s Tale") == ["luckys", "tale"]
    assert search_words("") == []


def test_search_words_fold_letters_without_an_accent_mark() -> None:
    # These letters don't decompose, so stripping accents alone keeps them.
    assert search_words("Ørsted Æble Łódź Œuvre Đakovo") == [
        "orsted",
        "aeble",
        "lodz",
        "oeuvre",
        "dakovo",
    ]
    assert search_words("Lucky\u2018s Tale") == ["luckys", "tale"]
