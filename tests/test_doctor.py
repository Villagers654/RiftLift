from __future__ import annotations

import os
from pathlib import Path

from riftlift.config import Game, Paths
from riftlift.diagnostics import (
    launch_log_path,
    launch_finished,
    launch_started,
    prune_diagnostic_logs,
    recent_launches,
    redact,
    trim_diagnostic_log,
)
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
    assert all(record.get("started_at") for record in records)
    assert all(
        record.get("finished_at")
        for record in records
        if record.get("event") == "finished"
    )


def test_recent_launches_do_not_keep_stale_failures_forever(tmp_path: Path) -> None:
    test_paths = paths(tmp_path)
    sample = game(tmp_path)
    failed, started = launch_started(
        test_paths, sample, "openxr", wrapper=False, capabilities=[]
    )
    launch_finished(test_paths, failed, started, exit_code=1)
    for _index in range(6):
        successful, started = launch_started(
            test_paths, sample, "openxr", wrapper=False, capabilities=[]
        )
        launch_finished(test_paths, successful, started, exit_code=0)

    records = recent_launches(test_paths)

    assert len(records) == 5
    assert all(record.get("exit_code") == 0 for record in records)


def test_diagnostic_log_is_compacted_to_bounded_tail(tmp_path: Path) -> None:
    target = tmp_path / "large.log"
    target.write_bytes(b"discard me\n" + b"x" * 100 + b"\nkeep me\n")

    trim_diagnostic_log(target, 64)

    payload = target.read_bytes()
    assert len(payload) <= 64
    assert payload.startswith(b"[older diagnostic output truncated]\n")
    assert payload.endswith(b"keep me\n")


def test_diagnostic_logs_are_count_and_size_bounded(tmp_path: Path) -> None:
    directory = tmp_path / "logs"
    directory.mkdir()
    for index in range(5):
        target = directory / f"launch-{index}.log"
        target.write_bytes((f"log {index}\n".encode()) * 100)
        os.utime(target, (index, index))

    prune_diagnostic_logs(directory, "launch-*.log", keep=2, max_bytes=128)

    retained = sorted(directory.glob("launch-*.log"))
    assert [item.name for item in retained] == ["launch-3.log", "launch-4.log"]
    assert all(item.stat().st_size <= 128 for item in retained)


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
    journal_queries = []
    monkeypatch.setattr(
        "riftlift.doctor._recent_journal_errors",
        lambda since=None: journal_queries.append(since) or ["XR_ERROR failed"],
    )
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
    assert journal_queries == ["2026-01-01T00:00:00+00:00"]
    assert "Test Controller" in report
    assert "shown launches: 0 successful, 1 failed/interrupted" in report
    assert not healthy  # Missing components are correctly visible as failures.


def test_build_report_includes_saved_launch_log_errors(
    tmp_path: Path, monkeypatch
) -> None:
    test_paths = paths(tmp_path)
    launch_id = "abc123"
    target = launch_log_path(test_paths, launch_id)
    target.parent.mkdir(parents=True)
    target.write_text("ordinary output\nOpenXR failed to create session\n")
    monkeypatch.setattr(
        "riftlift.doctor._runtime_description", lambda: (True, "test runtime")
    )
    monkeypatch.setattr("riftlift.doctor.steam_root", lambda: tmp_path / "steam")
    monkeypatch.setattr("riftlift.doctor._gpu_summary", lambda: "Test GPU")
    monkeypatch.setattr("riftlift.doctor._vulkan_summary", lambda: "Test Vulkan")
    monkeypatch.setattr("riftlift.doctor._connected_inputs", lambda: "none")
    monkeypatch.setattr("riftlift.doctor._service_state", lambda _name: "inactive")
    monkeypatch.setattr("riftlift.doctor._recent_journal_errors", lambda _since: [])
    monkeypatch.setattr("riftlift.doctor._recent_game_log_errors", lambda _paths: [])
    monkeypatch.setattr(
        "riftlift.doctor.recent_launches",
        lambda _paths: [
            {
                "id": launch_id,
                "event": "finished",
                "started_at": "2026-01-01T00:00:00+00:00",
                "game": "Sample",
                "backend": "openxr",
                "exit_code": 1,
                "duration_seconds": 1.0,
                "capabilities": [],
            }
        ],
    )

    report, _healthy = build_report(test_paths)

    assert "OpenXR failed to create session" in report
    assert "ordinary output" not in report


def test_build_report_ignores_known_xrizer_utility_probe(
    tmp_path: Path, monkeypatch
) -> None:
    test_paths = paths(tmp_path)
    launch_id = "utility-probe"
    target = launch_log_path(test_paths, launch_id)
    target.parent.mkdir(parents=True)
    target.write_text("Unsupported application type: Utility\n")
    monkeypatch.setattr(
        "riftlift.doctor._runtime_description", lambda: (True, "test runtime")
    )
    monkeypatch.setattr("riftlift.doctor.steam_root", lambda: tmp_path / "steam")
    monkeypatch.setattr("riftlift.doctor._gpu_summary", lambda: "Test GPU")
    monkeypatch.setattr("riftlift.doctor._vulkan_summary", lambda: "Test Vulkan")
    monkeypatch.setattr("riftlift.doctor._connected_inputs", lambda: "none")
    monkeypatch.setattr("riftlift.doctor._service_state", lambda _name: "inactive")
    monkeypatch.setattr("riftlift.doctor._recent_journal_errors", lambda _since: [])
    monkeypatch.setattr("riftlift.doctor._recent_game_log_errors", lambda _paths: [])
    monkeypatch.setattr(
        "riftlift.doctor.recent_launches",
        lambda _paths: [
            {
                "id": launch_id,
                "event": "finished",
                "started_at": "2026-01-01T00:00:00+00:00",
                "game": "Sample",
                "backend": "openxr",
                "exit_code": 0,
                "duration_seconds": 1.0,
                "capabilities": [],
            }
        ],
    )

    report, _healthy = build_report(test_paths)

    assert "Unsupported application type: Utility" not in report
    assert "No matching errors found during the recorded launch window" in report


def test_build_report_includes_saved_proton_log_errors(
    tmp_path: Path, monkeypatch
) -> None:
    test_paths = paths(tmp_path)
    target = test_paths.data / "diagnostics/proton/steam-123.log"
    target.parent.mkdir(parents=True)
    target.write_text("ordinary output\nerr:module:failed to load bridge\n")
    test_paths.config.mkdir(parents=True)
    (test_paths.config / "debug-logging").write_text("1\n")
    monkeypatch.setattr(
        "riftlift.doctor._runtime_description", lambda: (True, "test runtime")
    )
    monkeypatch.setattr("riftlift.doctor.steam_root", lambda: tmp_path / "steam")
    monkeypatch.setattr("riftlift.doctor._gpu_summary", lambda: "Test GPU")
    monkeypatch.setattr("riftlift.doctor._vulkan_summary", lambda: "Test Vulkan")
    monkeypatch.setattr("riftlift.doctor._connected_inputs", lambda: "none")
    monkeypatch.setattr("riftlift.doctor._service_state", lambda _name: "inactive")
    monkeypatch.setattr("riftlift.doctor.recent_launches", lambda _paths: [])
    monkeypatch.setattr("riftlift.doctor._recent_journal_errors", lambda _since: [])
    monkeypatch.setattr("riftlift.doctor._recent_game_log_errors", lambda _paths: [])

    report, _healthy = build_report(test_paths)

    assert "err:module:failed to load bridge" in report
    assert "ordinary output" not in report
    assert "Debug logging: enabled (bounded Proton diagnostic logs)" in report


def test_build_report_does_not_attribute_unrelated_journal_errors_without_launch(
    tmp_path: Path, monkeypatch
) -> None:
    test_paths = paths(tmp_path)
    queries = []
    monkeypatch.setattr(
        "riftlift.doctor._runtime_description", lambda: (True, "test runtime")
    )
    monkeypatch.setattr("riftlift.doctor.steam_root", lambda: tmp_path / "steam")
    monkeypatch.setattr("riftlift.doctor._gpu_summary", lambda: "Test GPU")
    monkeypatch.setattr("riftlift.doctor._vulkan_summary", lambda: "Test Vulkan")
    monkeypatch.setattr("riftlift.doctor._connected_inputs", lambda: "none")
    monkeypatch.setattr("riftlift.doctor._service_state", lambda _name: "inactive")
    monkeypatch.setattr("riftlift.doctor.recent_launches", lambda _paths: [])
    monkeypatch.setattr(
        "riftlift.doctor._recent_journal_errors",
        lambda since=None: queries.append(since) or [],
    )
    monkeypatch.setattr("riftlift.doctor._recent_game_log_errors", lambda _paths: [])

    report, _healthy = build_report(test_paths)

    assert queries == [None]
    assert "No structured launch history yet" in report
    assert "Journal scan skipped: no RiftLift launch window exists" in report
    assert "Launch the affected game through RiftLift" in report


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
