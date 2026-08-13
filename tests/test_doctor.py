from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from riftlift import __version__
from riftlift.config import Game, Paths
from riftlift.diagnostics import (
    collect_game_logs,
    launch_log_path,
    launch_finished,
    launch_started,
    prune_diagnostic_logs,
    recent_launches,
    redact,
    trim_diagnostic_log,
)
from riftlift.doctor import _likely_cause, build_report, upload_report


@pytest.fixture(autouse=True)
def isolate_host_diagnostic_sources(monkeypatch) -> None:
    for name in (
        "_recent_journal_errors",
        "_recent_kernel_errors",
        "_recent_coredumps",
        "_recent_steam_log_errors",
    ):
        monkeypatch.setattr(f"riftlift.doctor.{name}", lambda *_args: [])


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


def test_likely_cause_identifies_unavailable_monado_service() -> None:
    cause = _likely_cause(
        [
            "RiftLift: xrEnumerateInstanceExtensionProperties failed with "
            "OpenXR result -51"
        ],
        [],
    )

    assert "runtime service was unavailable" in cause[0]
    assert "Start the XR service in Envision" in cause[0]


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
        test_paths,
        sample,
        "openxr",
        wrapper=False,
        capabilities=["unreal"],
        components={"riftlift": __version__, "proton": "test-proton"},
        expected_components={"riftlift": __version__, "proton": "test-proton"},
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
    assert all(record.get("riftlift_version") == __version__ for record in records)
    assert any(
        record.get("components", {}).get("proton") == "test-proton"
        for record in records
    )
    assert any(
        record.get("expected_components", {}).get("proton") == "test-proton"
        for record in records
    )
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
    assert payload.startswith(b"discard me\n")
    assert b"[middle diagnostic output truncated]" in payload
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


def test_debug_capture_saves_recent_game_owned_logs_with_bounded_context(
    tmp_path: Path,
) -> None:
    test_paths = paths(tmp_path)
    sample = game(tmp_path)
    log_directory = sample.game_dir / "_local/r14logs"
    log_directory.mkdir(parents=True)
    source = log_directory / "client.log"
    source.write_bytes(b"header\n" + b"x" * (5 * 1024 * 1024) + b"\nlogin failed\n")

    collect_game_logs(test_paths, sample, "launch123", source.stat().st_mtime - 1)

    saved = list((test_paths.data / "diagnostics/game").iterdir())
    assert len(saved) == 1
    assert saved[0].stat().st_size <= 4 * 1024 * 1024
    payload = saved[0].read_bytes()
    assert payload.startswith(b"header\n")
    assert b"middle game log output truncated" in payload
    assert payload.endswith(b"login failed\n")


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
    monkeypatch.setattr("riftlift.doctor._recent_game_log_errors", lambda *_args: [])
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
                "riftlift_version": "0.8.0",
                "components": {
                    "riftlift": "0.8.0",
                    "compat_runtime": "old-compat",
                    "openvr_runtime": "old-openvr",
                    "proton": "old-proton",
                    "meta_horizon_link": "204.0",
                    "platform_bridge": "old-bridge",
                },
                "expected_components": {
                    "riftlift": "0.8.0",
                    "compat_runtime": "old-compat",
                },
            }
        ],
    )

    report, healthy = build_report(test_paths)

    assert "[System]" in report
    assert "psvr2-fossvr.service" not in report
    assert "psvr2-fossvr-wayvr.service" not in report
    assert "wivrn-server.service" not in report
    assert "[Recent launches]" in report
    assert f"Doctor build: RiftLift {__version__}" in report
    assert "captured components: riftlift=0.8.0" in report
    assert "CHANGED riftlift: launch=0.8.0; doctor=" in report
    assert "Evidence launch RiftLift build: 0.8.0" in report
    assert "Sample  openvr  exit 1 after 2.5s" in report
    assert "XR_ERROR failed" in report
    assert journal_queries == ["2026-01-01T00:00:00+00:00"]
    assert "Test Controller" in report
    assert "shown launches: 0 successful, 1 failed/incomplete" in report
    assert not healthy  # Missing components are correctly visible as failures.


def test_runtime_description_reports_selected_envision_profile(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = tmp_path / "openxr_monado.json"
    manifest.write_text(
        '{"runtime":{"name":"Monado","library_path":"libopenxr_monado.so"}}'
    )
    profile = type(
        "Profile",
        (),
        {
            "manifest": manifest.resolve(),
            "name": "Clean Profile",
            "uuid": "clean-id",
            "environment": {"DRI_PRIME": "1", "XRT_COMPOSITOR_COMPUTE": "1"},
        },
    )()
    monkeypatch.setattr(
        "riftlift.doctor.active_runtime_json", lambda: manifest.resolve()
    )
    monkeypatch.setattr("riftlift.doctor.envision_profile", lambda: profile)

    from riftlift.doctor import _runtime_description

    ok, detail = _runtime_description()
    assert ok
    assert "Envision profile Clean Profile [clean-id]" in detail
    assert "environment=DRI_PRIME,XRT_COMPOSITOR_COMPUTE" in detail


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
    monkeypatch.setattr("riftlift.doctor._recent_game_log_errors", lambda *_args: [])
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
    monkeypatch.setattr("riftlift.doctor._recent_game_log_errors", lambda *_args: [])
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
    monkeypatch.setattr(
        "riftlift.doctor.recent_launches",
        lambda _paths: [
            {
                "event": "started",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "game": "Sample",
                "backend": "openxr",
                "capabilities": [],
                "debug_logging": True,
            }
        ],
    )
    monkeypatch.setattr("riftlift.doctor._recent_journal_errors", lambda _since: [])
    monkeypatch.setattr("riftlift.doctor._recent_game_log_errors", lambda *_args: [])

    report, _healthy = build_report(test_paths)

    assert "err:module:failed to load bridge" in report
    assert "ordinary output" not in report
    assert "Debug logging: enabled (expanded bounded capture)" in report
    assert "required runtime module or game file failed to load" in report
    assert report.index("[Likely cause]") < report.index("[Recent error evidence]")


def test_build_report_ignores_proton_logs_older_than_launch_window(
    tmp_path: Path, monkeypatch
) -> None:
    test_paths = paths(tmp_path)
    target = test_paths.data / "diagnostics/proton/steam-old.log"
    target.parent.mkdir(parents=True)
    target.write_text("err: DxvkSubmissionQueue: VK_ERROR_DEVICE_LOST\n")
    os.utime(target, (1, 1))
    monkeypatch.setattr(
        "riftlift.doctor._runtime_description", lambda: (True, "test runtime")
    )
    monkeypatch.setattr("riftlift.doctor.steam_root", lambda: tmp_path / "steam")
    monkeypatch.setattr("riftlift.doctor._gpu_summary", lambda: "Test GPU")
    monkeypatch.setattr("riftlift.doctor._vulkan_summary", lambda: "Test Vulkan")
    monkeypatch.setattr("riftlift.doctor._connected_inputs", lambda: "none")
    monkeypatch.setattr("riftlift.doctor._service_state", lambda _name: "inactive")
    monkeypatch.setattr(
        "riftlift.doctor.recent_launches",
        lambda _paths: [
            {
                "event": "started",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "game": "Sample",
                "backend": "openxr",
                "capabilities": [],
            }
        ],
    )
    monkeypatch.setattr("riftlift.doctor._recent_journal_errors", lambda _since: [])
    monkeypatch.setattr("riftlift.doctor._recent_game_log_errors", lambda *_args: [])

    report, _healthy = build_report(test_paths)

    assert "VK_ERROR_DEVICE_LOST" not in report


def test_build_report_does_not_attribute_unrelated_journal_errors_without_launch(
    tmp_path: Path, monkeypatch
) -> None:
    test_paths = paths(tmp_path)
    queries = []
    monkeypatch.setattr(
        "riftlift.doctor._runtime_description", lambda: (True, "test runtime")
    )
    monkeypatch.setattr("riftlift.doctor.steam_root", lambda: tmp_path / "steam")
    monkeypatch.setattr(
        "riftlift.doctor.proton_dir",
        lambda: (_ for _ in ()).throw(RuntimeError("Steam is unavailable")),
    )
    monkeypatch.setattr("riftlift.doctor._gpu_summary", lambda: "Test GPU")
    monkeypatch.setattr("riftlift.doctor._vulkan_summary", lambda: "Test Vulkan")
    monkeypatch.setattr("riftlift.doctor._connected_inputs", lambda: "none")
    monkeypatch.setattr("riftlift.doctor._service_state", lambda _name: "inactive")
    monkeypatch.setattr("riftlift.doctor.recent_launches", lambda _paths: [])
    monkeypatch.setattr(
        "riftlift.doctor._recent_journal_errors",
        lambda since=None: queries.append(since) or [],
    )
    monkeypatch.setattr("riftlift.doctor._recent_game_log_errors", lambda *_args: [])

    report, _healthy = build_report(test_paths)

    assert queries == [None]
    assert "No structured launch history yet" in report
    assert "proton: installed=missing; expected=" in report
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
