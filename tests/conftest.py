import pytest


@pytest.fixture(autouse=True)
def _assume_compatibility_runtime_is_ready(monkeypatch):
    """Keep unrelated GUI tests from triggering a real background setup() run.

    Window.__init__ now auto-starts the compatibility setup when needed_setup()
    is true, which would otherwise make every unrelated test that constructs a
    Window spawn a background thread doing real network installs.
    """
    monkeypatch.setattr("riftlift.main_window.needs_setup", lambda _paths: False)
