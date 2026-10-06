import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from riftlift import windows
from riftlift.config import Paths
from riftlift.meta_auth import windows_callback_command
from riftlift.util import RiftLiftError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows packaging")


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("RIFTLIFT_HOME", str(tmp_path / "user data"))
    monkeypatch.delenv("RIFTLIFT_WINDOWS_RUNTIME", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "app files"), raising=False)
    paths = Paths.defaults()
    native = windows.runtime_dir(paths)
    native.mkdir(parents=True)
    for name in windows.FILES | windows.PLATFORM_FILES:
        (native / name).write_bytes(b"test payload")
    return paths, native


def test_bundled_setup_is_offline_and_preserves_payload(bundle, monkeypatch):
    paths, native = bundle
    monkeypatch.setattr(
        windows.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network")
    )
    assert windows.install_payload(paths) == native
    assert (native / "RiftLiftOpenVR64.dll").read_bytes() == b"test payload"


def test_incomplete_bundle_requests_reinstall_without_download(bundle, monkeypatch):
    paths, native = bundle
    (native / "LibOVRPlatformImpl64_1.dll").unlink()
    monkeypatch.setattr(
        windows.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network")
    )
    with pytest.raises(RiftLiftError, match="reinstall"):
        windows.install_payload(paths)


def test_bundled_discovery_needs_no_first_launch_download(bundle, monkeypatch):
    paths, native = bundle
    monkeypatch.setattr(windows, "sha256", lambda p: windows.SDK_RUNTIME_SHA256)
    monkeypatch.setattr(windows, "download", lambda *a, **k: pytest.fail("network"))
    assert windows.install_sdk_runtime(paths) == native


def test_packaged_callback_uses_exe_and_quotes_spaces(bundle, monkeypatch):
    paths, _ = bundle
    exe = Path("C:/Program Files/RiftLift/RiftLift.exe")
    monkeypatch.setattr(sys, "executable", str(exe))
    target, command = windows_callback_command(paths)
    assert target == exe
    assert command.startswith(f'"{exe}" --home "')
    assert command.endswith(' callback "%1"')
    assert "-m" not in command


def test_source_callback_uses_python_module(tmp_path, monkeypatch):
    monkeypatch.setenv("RIFTLIFT_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    _, command = windows_callback_command(Paths.defaults())
    assert "-m riftlift.windows" in command


def test_console_free_worker_reads_request_and_finalization_ack(tmp_path):
    script = tmp_path / "fixture_worker.py"
    script.write_text(
        "from riftlift import library, windows_app\n"
        "from riftlift.config import Game\n"
        "import sys\n"
        "def add(paths, url, *, on_finalizing):\n"
        "    on_finalizing()\n"
        "    game = Game('fixture', 'Fixture', '1', 'fixture', str(paths.games), 'fixture.exe', [])\n"
        "    game.save(paths)\n"
        "    return game\n"
        "library.add = add\n"
        "sys.argv = ['fixture', '--download-worker']\n"
        "raise SystemExit(windows_app.main())\n",
        encoding="utf-8",
    )
    request = {
        "paths": {
            key: str(tmp_path / key)
            for key in ("data", "cache", "config", "games", "prefix", "tools")
        },
        "url": "123456789",
        "sync_steam": False,
    }
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [str(Path(sys.executable).with_name("pythonw.exe")), str(script)],
        input=(json.dumps(request) + '\n{"event":"finalize"}\n').encode(),
        capture_output=True,
        timeout=10,
        env=environment,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert [json.loads(line)["event"] for line in result.stdout.splitlines()] == [
        "finishing",
        "complete",
    ]
    assert (tmp_path / "data/games/fixture.json").is_file()
    assert not (tmp_path / "data/logs").exists()
