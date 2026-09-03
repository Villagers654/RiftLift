"""Native loader regressions: public SDK utility names have no underscore."""

import runpy
from pathlib import Path

import pytest

generate = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts/platform-exports.py")
)["generate_forwarders"]


def test_native_forwarders_include_utility_exports_and_preserve_overrides():
    symbols = """
      1    0 00001000 ovrID_FromString
      2    1 00002000 ovrKeyValuePair_makeString
      3    2 00003000 ovr_PlatformInitializeWindows
      4    3 00004000 ovr_User_GetID
"""
    output = generate(
        symbols,
        "__declspec(dllexport) int __cdecl ovr_PlatformInitializeWindows(const char* id)",
    )
    assert "ovrID_FromString=LibOVRPlatformImpl64_1_real.ovrID_FromString" in output
    assert (
        "ovrKeyValuePair_makeString=LibOVRPlatformImpl64_1_real.ovrKeyValuePair_makeString"
        in output
    )
    assert "ovr_User_GetID=LibOVRPlatformImpl64_1_real.ovr_User_GetID" in output
    assert "ovr_PlatformInitializeWindows" not in output


def test_native_forwarder_generation_rejects_an_empty_export_table():
    with pytest.raises(ValueError, match="No platform exports"):
        generate("invalid dumpbin output", "")
