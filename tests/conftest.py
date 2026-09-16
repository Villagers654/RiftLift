import gc

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


@pytest.fixture(autouse=True)
def _collect_qt_garbage_between_tests():
    """Destroy each test's Qt widgets before the next one instead of letting
    them pile up for one big, unpredictable collection later.

    PySide6/shiboken assumes a C++ parent-child chain (e.g. a QBoxLayout
    inside a QWidget) is torn down before Python's own garbage collector
    gets around to freeing the matching wrapper objects. With hundreds of
    GUI tests each building their own Window, leaving that to whichever GC
    pass happens to run last (often at interpreter shutdown) can destroy an
    accumulated backlog in an order Qt doesn't expect, double-freeing a
    layout and crashing the whole process (SIGSEGV/SIGBUS) - reproduced
    locally, and only by running the full suite, not any single test.
    Collecting right after each test keeps each cleanup small and in an
    order Qt has already seen many times over without incident.
    """
    yield
    gc.collect()
