import sys
from pathlib import Path

import pytest

from riftlift import windows
from riftlift.config import Game, Paths
from riftlift.util import RiftLiftError, atomic_write_text

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Native Windows host tests"
)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("RIFTLIFT_HOME", str(tmp_path))
    return Paths.defaults()


def test_windows_paths_are_portable(paths, tmp_path):
    assert paths.data == tmp_path / "data"
    assert paths.config == tmp_path / "config"


def test_atomic_write_windows(tmp_path):
    target = tmp_path / "atomic.txt"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text() == "second"
    assert list(tmp_path.iterdir()) == [target]


def test_cli_help_has_native_backend(capsys):
    from riftlift.cli import main

    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
    assert "native Windows" in capsys.readouterr().out


def test_windows_uses_the_shared_product_version(capsys):
    from riftlift import __version__
    from riftlift.cli import main

    with pytest.raises(SystemExit) as result:
        main(["--version"])
    assert result.value.code == 0
    assert capsys.readouterr().out == f"riftlift {__version__}\n"


def test_windows_metadata_is_part_of_the_main_package():
    root = Path(__file__).resolve().parents[1]
    metadata = (root / "pyproject.toml").read_text()
    assert '"Operating System :: Microsoft :: Windows"' in metadata
    assert not (root / "WINDOWS.md").exists()


def test_add_local_preserves_game_binary(paths):
    executable = Path(sys.executable)
    before = executable.read_bytes()
    game = windows.add_local(paths, str(executable), "Smoke probe")
    assert Game.load(paths, game.slug).executable_path == executable
    assert not game.platform_shim and not game.platform_offline
    assert executable.read_bytes() == before


def test_add_local_rejects_non_pe(paths, tmp_path):
    fake = tmp_path / "fake.exe"
    fake.write_text("not a binary")
    with pytest.raises(RiftLiftError, match="x64 PE"):
        windows.add_local(paths, str(fake))


def test_payload_checksum_failure_writes_nothing(paths, tmp_path):
    fake = tmp_path / "fake.zip"
    fake.write_bytes(b"bad")
    with pytest.raises(RiftLiftError, match="SHA256"):
        windows.install_payload(paths, fake)
    assert not windows.runtime_dir(paths).exists()


def test_native_launch_builds_argv_without_shell_or_wine(paths):
    game = windows.add_local(paths, sys.executable, "Probe", arguments='"two words"')
    runtime = windows.runtime_dir(paths)
    runtime.mkdir(parents=True)
    for name in windows.FILES:
        (runtime / name).touch()
    argv = windows.launch_command(paths, game, "openxr", ["tail"])
    assert argv[1:4] == ["/openxr", "/wait", "/cwd"]
    assert argv[-2:] == ["two words", "tail"]
    assert "wine" not in argv and "proton" not in argv
    assert "LibOVRPlatformImpl64_1.dll" not in " ".join(argv)


def test_missing_runtime_stops_before_launch(paths, monkeypatch):
    game = windows.add_local(paths, sys.executable, "Probe")
    runtime = windows.runtime_dir(paths)
    runtime.mkdir(parents=True)
    for name in windows.FILES:
        (runtime / name).touch()
    monkeypatch.setattr(windows, "runtime_ready", lambda backend: False)
    monkeypatch.setattr(
        windows.subprocess, "run", lambda *a, **k: pytest.fail("started process")
    )
    with pytest.raises(RiftLiftError, match="connect the headset"):
        windows.launch(paths, game, "openxr")


def test_doctor_missing_runtime_is_not_success(paths, monkeypatch):
    monkeypatch.setattr(windows, "active_openxr", lambda: None)
    monkeypatch.setattr(windows, "active_openvr", lambda: None)
    report, code = windows.doctor(paths)
    assert code == 2
    assert "NOT REGISTERED" in report
    assert "NOT VERIFIED" in report


def test_gui_constructs_without_linux_imports(paths, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from riftlift.main_window import Window

    app = QApplication.instance() or QApplication([])
    window = Window()
    assert window.windowTitle() == "RiftLift"
    assert Window.__module__ == "riftlift.main_window"
    assert not window.signin.isEnabled()
    assert not window.steam_games.isEnabled()
    assert window.library.count() == 0
    window.close()
    app.processEvents()


def test_shared_ui_backend_reports_failures(paths, monkeypatch):
    from riftlift import windows_ui_backend

    monkeypatch.setattr(windows, "doctor", lambda p: ("Missing runtime", 2))
    with pytest.raises(RiftLiftError, match="View Activity"):
        windows_ui_backend.doctor(paths)
    game = windows.add_local(paths, sys.executable, "Probe")
    monkeypatch.setattr(windows, "runtime_ready", lambda b: True)
    monkeypatch.setattr(windows, "launch", lambda *a, **k: 7)
    with pytest.raises(RiftLiftError, match="code 7"):
        windows_ui_backend.launch(paths, game, [])


def test_windows_playtime_locks_across_processes(paths):
    import subprocess

    from riftlift.playtime import playtime

    script = (
        "from riftlift.config import Paths; from riftlift.playtime import mark_launch; "
        "[mark_launch(Paths.defaults(), 'probe') for _ in range(5)]"
    )
    children = [subprocess.Popen([sys.executable, "-c", script]) for _ in range(3)]
    assert all(child.wait(timeout=30) == 0 for child in children)
    assert playtime(paths, "probe").launches == 15
