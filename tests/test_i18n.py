from riftlift.i18n import _flatten, en, fr, namespace, set_language, tr


def test_every_language_has_the_same_keys_as_english() -> None:
    assert set(_flatten(fr.STRINGS)) == set(_flatten(en.STRINGS))


def test_tr_falls_back_to_english_for_an_unknown_key() -> None:
    set_language("fr")
    assert tr("does.not.exist") == "does.not.exist"
    set_language("en")


def test_tr_switches_language() -> None:
    set_language("fr")
    assert tr("game.launch") == "Lancer en VR"
    set_language("en")
    assert tr("game.launch") == "Launch in VR"


def test_namespace_scopes_bare_keys() -> None:
    nav = namespace("nav")
    set_language("fr")
    assert nav("settings") == "Paramètres"
    set_language("en")
    assert nav("settings") == "Settings"
