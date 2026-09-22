import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from riftlift.runtime import validate_openvr_library
from riftlift.util import RiftLiftError


@pytest.fixture
def openvr_library(tmp_path, request):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("a C compiler is needed for the native loader regression")
    library = tmp_path / "native library.so"
    subprocess.run(
        [compiler, "-shared", "-fPIC", "-x", "c", "-", "-o", str(library)],
        input=getattr(
            request,
            "param",
            "void HmdSystemFactory(void) {}\nvoid VRClientCoreFactory(void) {}\n",
        ),
        text=True,
        check=True,
    )
    return library


@pytest.fixture
def appimage_launcher(tmp_path, monkeypatch):
    # python-appimage's _initappimage replaces sys.executable with the app
    # launcher. Reproduce its routing, including paths containing spaces.
    launcher = tmp_path / "RiftLift test.AppImage"
    launcher.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " -c 'from riftlift.cli import main; raise SystemExit(main())' \"$@\"\n"
    )
    launcher.chmod(0o755)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parents[1] / "src"))
    monkeypatch.setenv("APPDIR", str(tmp_path))
    monkeypatch.setenv("APPIMAGE_COMMAND", str(launcher))
    monkeypatch.setattr(sys, "executable", str(launcher))
    return launcher


def test_appimage_launcher_reproduces_python_command_routing(appimage_launcher):
    result = subprocess.run(
        [str(appimage_launcher), "-c", "import ctypes, sys"],
        capture_output=True,
        text=True,
        env=os.environ,
    )
    assert result.returncode == 2
    assert "invalid choice: 'import ctypes, sys'" in result.stderr


def test_appimage_validates_native_library(appimage_launcher, openvr_library):
    validate_openvr_library(openvr_library)


@pytest.mark.parametrize(
    "openvr_library,missing_symbol",
    [
        ("void HmdSystemFactory(void) {}", "VRClientCoreFactory"),
        ("void VRClientCoreFactory(void) {}", "HmdSystemFactory"),
    ],
    indirect=["openvr_library"],
)
def test_appimage_requires_both_factories(
    appimage_launcher, openvr_library, missing_symbol
):
    with pytest.raises(RiftLiftError, match=missing_symbol):
        validate_openvr_library(openvr_library)


def test_appimage_rejects_invalid_library(appimage_launcher, tmp_path):
    library = tmp_path / "invalid.so"
    library.write_bytes(b"not an ELF library")
    with pytest.raises(RiftLiftError, match="XRizer cannot load") as error:
        validate_openvr_library(library)
    assert "invalid choice" not in str(error.value)
    assert str(library) in str(error.value)


def test_regular_python_validates_native_library(monkeypatch, openvr_library):
    monkeypatch.delenv("APPDIR", raising=False)
    validate_openvr_library(openvr_library)
