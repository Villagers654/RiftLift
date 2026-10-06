"""Verify both route choices locally; these do not execute a Linux runtime."""

import sys
from types import SimpleNamespace

from riftlift import cli, desktop_services


def test_linux_parser_remains_available(monkeypatch):
    monkeypatch.setattr(cli, "os", SimpleNamespace(name="posix"))
    parser = cli.parser()
    assert parser.parse_args(["gui"]).command == "gui"
    assert parser.parse_args(["steam-sync"]).command == "steam-sync"
    assert "Linux" in parser.description


def test_linux_desktop_service_delegates_to_existing_backend(monkeypatch):
    received = []
    monkeypatch.setattr(desktop_services, "os", SimpleNamespace(name="posix"))
    monkeypatch.setitem(
        sys.modules,
        "riftlift.launch",
        SimpleNamespace(launch=lambda *args: received.append(args) or 23),
    )
    assert desktop_services.launch("fixture paths", "fixture game", ["argument"]) == 23
    assert received == [("fixture paths", "fixture game", ["argument"])]
    assert desktop_services.supports_steam_import()


def test_windows_service_accepts_openvr_without_openxr(monkeypatch):
    monkeypatch.setattr(desktop_services, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        desktop_services,
        "_windows",
        lambda: SimpleNamespace(
            active_openxr=lambda: None, active_openvr=lambda: "fixture SteamVR"
        ),
    )
    assert desktop_services.active_runtime_json() == "fixture SteamVR"
    assert not desktop_services.supports_steam_import()
