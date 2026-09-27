"""Key-based UI translations, with automatic system-locale detection.

Each language has its own module (`en.py`, `fr.py`, ...) with strings
nested by namespace, e.g. STRINGS["nav"]["settings"]. A call site scopes
to one namespace once, the same way `useTranslation("nav")` does in
react-i18next, then looks up bare keys within it:

    NAV = namespace("nav")
    NAV("settings")  # -> "Settings" / "Paramètres"

Editing a display string never breaks another language, since lookups
always go through the stable (namespace, key) pair, never the text.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QLocale

from . import en, fr

LANGUAGES = {"auto": "Auto", "en": "English", "fr": "Français"}


def _flatten(table: dict, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in table.items():
        full_key = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{full_key}."))
        else:
            flat[full_key] = value
    return flat


_TABLES: dict[str, dict[str, str]] = {
    "en": _flatten(en.STRINGS),
    "fr": _flatten(fr.STRINGS),
}

_current = "en"


def detect_system_language() -> str:
    code = QLocale.system().name().split("_")[0]
    return code if code in _TABLES else "en"


def set_language(preference: str) -> None:
    global _current
    _current = detect_system_language() if preference == "auto" else preference


def current_language() -> str:
    return _current


def tr(key: str) -> str:
    table = _TABLES.get(_current, _TABLES["en"])
    return table.get(key, _TABLES["en"].get(key, key))


def namespace(prefix: str) -> Callable[[str], str]:
    """Return a `tr` scoped to one namespace, e.g. NAV("settings") == tr("nav.settings")."""

    def scoped(key: str) -> str:
        return tr(f"{prefix}.{key}")

    return scoped
