import pytest

from riftlift.i18n import set_language


@pytest.fixture(autouse=True)
def _assume_compatibility_runtime_is_ready(monkeypatch):
    """Keep unrelated GUI tests from triggering a real background setup() run.

    Window.__init__ now auto-starts the compatibility setup when needed_setup()
    is true, which would otherwise make every unrelated test that constructs a
    Window spawn a background thread doing real network installs. Also stubs
    setup() itself, since a test can still legitimately flip needs_setup back
    to True for its own reasons (e.g. a setup banner's visibility) without
    meaning to trigger a real install - a test that actually wants to assert
    setup() was called re-patches it itself, which simply overrides this.
    """
    monkeypatch.setattr("riftlift.main_window.needs_setup", lambda _paths: False)
    monkeypatch.setattr("riftlift.main_window.setup", lambda _paths: None)


@pytest.fixture(autouse=True)
def _english_ui_language():
    """Keep assertions on English UI text stable regardless of the host locale."""
    set_language("en")
    yield
    set_language("en")
