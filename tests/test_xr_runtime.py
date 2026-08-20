import json
from pathlib import Path

from riftlift.xr_runtime import xr_build_components


def _runtime(tmp_path: Path, monkeypatch, library_path: str) -> Path:
    manifest = tmp_path / "runtime/openxr.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "file_format_version": "1.0.0",
                "runtime": {"name": "Test", "library_path": library_path},
            }
        )
    )
    monkeypatch.setenv("XR_RUNTIME_JSON", str(manifest))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return manifest


def test_runtime_component_resolves_relative_library_paths(tmp_path, monkeypatch):
    manifest = _runtime(tmp_path, monkeypatch, "lib/libopenxr_test.so")
    library = manifest.parent / "lib/libopenxr_test.so"
    library.parent.mkdir()
    library.write_bytes(b"runtime")

    components = xr_build_components()

    assert "libopenxr_test.so sha256:" in components["openxr_runtime"]


def test_runtime_component_leaves_search_path_library_names_alone(
    tmp_path, monkeypatch
):
    _runtime(tmp_path, monkeypatch, "libopenxr_test.so")

    components = xr_build_components()

    assert components["openxr_runtime"] == "Test: libopenxr_test.so"
