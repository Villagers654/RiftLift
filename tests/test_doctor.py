from __future__ import annotations

from pathlib import Path

from riftlift.config import Game, Paths
from riftlift.diagnostics import launch_finished, launch_started, recent_launches, redact
from riftlift.doctor import build_report, upload_report


def paths(tmp_path: Path) -> Paths:
    data = tmp_path / "data"
    return Paths(
        data,
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        data / "compatdata",
        data / "tools",
    )


def game(tmp_path: Path, name: str = "Sample") -> Game:
    directory = tmp_path / "games" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "Sample.exe").write_bytes(b"MZ")
    return Game(
        name.casefold(),
        name,
        "1",
        f"sample.{name.casefold()}",
        str(directory),
        "Sample.exe",
        [],
    )


def test_redact_removes_credentials_email_and_home(monkeypatch) -> None:
    monkeypatch.setattr("riftlift.diagnostics.Path.home", lambda: Path("/home/alice"))

    value = redact(
        "/home/alice/game /var/home/alice/game token=abcdef alice@example.com "
        r'C:\users\alice\AppData "access_token": "json-secret" '
        "Authorization: Bearer bearer-secret"
    )

    assert "abcdef" not in value
    assert "json-secret" not in value
    assert "bearer-secret" not in value
    assert "alice@example.com" not in value
    assert "/home/alice" not in value
    assert "/var/home/alice" not in value
    assert r"C:\users\alice" not in value


def test_recent_launches_preserve_failure_and_interruption(tmp_path: Path) -> None:
    test_paths = paths(tmp_path)
    sample = game(tmp_path)
    successful, started = launch_started(
        test_paths, sample, "openxr", wrapper=False, capabilities=["unreal"]
    )
    launch_finished(test_paths, successful, started, exit_code=0)
    failed, started = launch_started(
        test_paths, sample, "openvr", wrapper=True, capabilities=["openvr"]
    )
    launch_finished(test_paths, failed, started, exit_code=1)
    launch_started(test_paths, sample, "openvr", wrapper=True, capabilities=[])

    records = recent_launches(test_paths)

    assert any(record.get("exit_code") == 1 for record in records)
    assert any(record.get("event") == "started" for record in records)
    assert any(record.get("exit_code") == 0 for record in records)


def test_build_report_includes_recent_launch_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    test_paths = paths(tmp_path)
    sample = game(tmp_path)
    sample.save(test_paths)
    monkeypatch.setattr(
        "riftlift.doctor._runtime_description",
        lambda: (True, "~/.config/openxr/runtime.json (Monado; libopenxr_monado.so)"),
    )
    monkeypatch.setattr("riftlift.doctor.steam_root", lambda: tmp_path / "steam")
    monkeypatch.setattr("riftlift.doctor._gpu_summary", lambda: "Test GPU; amdgpu")
    monkeypatch.setattr("riftlift.doctor._vulkan_summary", lambda: "driverName = RADV")
    monkeypatch.setattr("riftlift.doctor._connected_inputs", lambda: "Test Controller")
    monkeypatch.setattr("riftlift.doctor._service_state", lambda _name: "active")
    monkeypatch.setattr("riftlift.doctor._recent_journal_errors", lambda: ["XR_ERROR failed"])
    monkeypatch.setattr("riftlift.doctor._recent_game_log_errors", lambda _paths: [])
    monkeypatch.setattr(
        "riftlift.doctor.recent_launches",
        lambda _paths: [
            {
                "event": "finished",
                "at": "2026-01-01T00:00:00+00:00",
                "game": "Sample",
                "backend": "openvr",
                "exit_code": 1,
                "duration_seconds": 2.5,
                "capabilities": ["openvr"],
            }
        ],
    )

    report, healthy = build_report(test_paths)

    assert "[System]" in report
    assert "[Recent launches]" in report
    assert "Sample  openvr  exit 1 after 2.5s" in report
    assert "XR_ERROR failed" in report
    assert "Test Controller" in report
    assert not healthy  # Missing components are correctly visible as failures.


def test_upload_report_posts_plain_text_and_returns_url(monkeypatch) -> None:
    captured = {}

    class Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b"https://paste.rs/abc123\n"

    def open_request(request, timeout):
        captured["data"] = request.data
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("riftlift.doctor.urllib.request.urlopen", open_request)

    assert upload_report("hello\n") == "https://paste.rs/abc123"
    assert captured == {"data": b"hello\n", "timeout": 10}
