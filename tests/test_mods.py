import pytest

from riftlift.mods import (
    apply_dll_overrides,
    configure_mod_loaders,
    validate_dll_overrides,
)


def test_saved_overrides_replace_only_matching_inherited_rules():
    environment = {"WINEDLLOVERRIDES": "d3d11,dxgi=n;*VERSION.DLL=b;winhttp=n,b"}
    apply_dll_overrides(environment, "version=n,b;dxgi=b;winhttp=")
    assert environment["WINEDLLOVERRIDES"] == "d3d11=n;version=n,b;dxgi=b;winhttp="


@pytest.mark.parametrize(
    "value", ["version", "version=x", "=n", "version=n;export X=1", "version=n\0"]
)
def test_rejects_invalid_dll_rules(value):
    with pytest.raises(ValueError):
        validate_dll_overrides(value)


@pytest.mark.parametrize(
    "folder,dll",
    [
        ("MelonLoader", "version.dll"),
        ("MelonLoader", "winmm.dll"),
        ("MelonLoader", "winhttp.dll"),
        ("BepInEx", "winhttp.dll"),
        ("MELONLOADER", "VERSION.DLL"),
    ],
)
def test_installed_loader_gets_native_override(tmp_path, folder, dll):
    (tmp_path / folder).mkdir()
    (tmp_path / dll).write_bytes(b"proxy")
    environment = {"WINEDLLOVERRIDES": "d3d11=n;dxgi=n"}

    configure_mod_loaders(environment, tmp_path / "Game.exe")

    assert environment["WINEDLLOVERRIDES"] == (
        f"d3d11=n;dxgi=n;{dll.lower().removesuffix('.dll')}=n,b"
    )


@pytest.mark.parametrize(
    "override", ["version=b", "VERSION.DLL=", "winhttp,version=b", "*version=b", "*=b"]
)
def test_explicit_override_wins(tmp_path, override):
    (tmp_path / "MelonLoader").mkdir()
    (tmp_path / "version.dll").touch()
    environment = {"WINEDLLOVERRIDES": override}

    configure_mod_loaders(environment, tmp_path / "Game.exe")

    assert environment == {"WINEDLLOVERRIDES": override}


@pytest.mark.parametrize("layout", ["dll-only", "folder-only", "wrong-types", "nested"])
def test_unrelated_or_incomplete_install_is_unchanged(tmp_path, layout):
    if layout == "dll-only":
        (tmp_path / "version.dll").touch()
    elif layout == "folder-only":
        (tmp_path / "MelonLoader").mkdir()
    elif layout == "wrong-types":
        (tmp_path / "MelonLoader").touch()
        (tmp_path / "version.dll").mkdir()
    else:
        nested = tmp_path / "backup"
        (nested / "MelonLoader").mkdir(parents=True)
        (nested / "version.dll").touch()
    environment = {"WINEDLLOVERRIDES": "dxgi=n"}

    configure_mod_loaders(environment, tmp_path / "Game.exe")

    assert environment == {"WINEDLLOVERRIDES": "dxgi=n"}


def test_detection_is_per_launch_and_does_not_leak_to_other_games(tmp_path):
    (tmp_path / "MelonLoader").mkdir()
    proxy = tmp_path / "version.dll"
    proxy.touch()
    environment = {}
    configure_mod_loaders(environment, tmp_path / "Game.exe")
    configure_mod_loaders(environment, tmp_path / "Game.exe")
    assert environment == {"WINEDLLOVERRIDES": "version=n,b"}

    other_environment = {}
    configure_mod_loaders(other_environment, tmp_path / "other/Game.exe")
    assert other_environment == {}

    proxy.unlink()
    next_environment = {}
    configure_mod_loaders(next_environment, tmp_path / "Game.exe")
    assert next_environment == {}
