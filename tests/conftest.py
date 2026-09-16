import pytest

from riftlift.i18n import set_language


@pytest.fixture(autouse=True)
def _assume_compatibility_runtime_is_ready(monkeypatch):
    """Keep unrelated GUI tests from triggering a real background setup() run.

    Window.__init__ now auto-starts the compatibility setup when needed_setup()
    is true, which would otherwise make every unrelated test that constructs a
    Window spawn a background thread doing real network installs.
    """
    monkeypatch.setattr("riftlift.main_window.needs_setup", lambda _paths: False)


@pytest.fixture(autouse=True)
def _english_ui_language():
    """Keep assertions on English UI text stable regardless of the host locale."""
    set_language("en")
    yield
    set_language("en")
