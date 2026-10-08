"""Exercise the production OpenXR input methods with a small fake runtime.

The PE bridge needs MSVC and the Oculus SDK to build in full. These tests compile
its unchanged input method bodies on the host so input regressions can also run
in the Linux application test job without a headset or a Windows SDK.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
RUNTIME = ROOT / "runtime/windows-openxr"


def _function(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


@pytest.mark.parametrize("release", [False, True], ids=["debug", "release"])
def test_openxr_menu_input(tmp_path: Path, release: bool):
    compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.skip("A C++ compiler is required for the OpenXR input harness")

    source = (RUNTIME / "InputManager.cpp").read_text()
    methods = "\n\n".join(
        _function(source, signature)
        for signature in (
            "bool InputManager::Action::GetDigital(",
            "void InputManager::OculusTouch::GetInputState(",
            "ovrResult InputManager::GetInputState(",
        )
    )
    common = (RUNTIME / "Common.h").read_text()
    check_xr = common.split("#define CHK_XR(x)", 1)[1].split("#define CHK_OVR", 1)[0]
    harness = (ROOT / "tests/native/openxr_input.cpp").read_text()
    translation_unit = tmp_path / "openxr_input.cpp"
    translation_unit.write_text(
        harness.replace(
            "// PRODUCTION_CHECK_XR", "#define CHK_XR(x)" + check_xr
        ).replace("// PRODUCTION_INPUT_METHODS", methods)
    )
    executable = tmp_path / "openxr_input"
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-missing-field-initializers",
            *(["-DNDEBUG", "-O2"] if release else []),
            str(translation_unit),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)
