import io
import json
import os
import sys
from pathlib import Path

if os.name != "nt":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from meta_pcvr_downloader.api import Build
from PySide6 import QtWidgets

from riftlift import download_worker
from riftlift.config import Game, Paths
from riftlift.download_job import DownloadJob
from riftlift.library import NotLaunchableError


def _paths(tmp_path: Path) -> Paths:
    paths = Paths(
        *(
            tmp_path / name
            for name in ("data", "cache", "config", "games", "prefix", "tools")
        )
    )
    paths.create()
    return paths


def _run_worker(monkeypatch, paths, build, acknowledgements):
    request = {
        "paths": {
            key: str(getattr(paths, key))
            for key in ("data", "cache", "config", "games", "prefix", "tools")
        },
        "url": "1369078409873402",
        "sync_steam": False,
        "build": build,
    }
    incoming = json.dumps(request) + "\n" + '{"event":"finalize"}\n' * acknowledgements
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(incoming))
    monkeypatch.setattr(sys, "stdout", output)
    code = download_worker.main()
    return code, [json.loads(line) for line in output.getvalue().splitlines()]


def test_worker_installs_a_chosen_build(tmp_path, monkeypatch) -> None:
    paths = _paths(tmp_path)
    calls = []

    def add(paths, url, *, build_selector, on_finalizing):
        calls.append(build_selector)
        on_finalizing()
        return Game("echo-vr-old", "Echo VR", "1", "k", str(tmp_path), "e.exe", [])

    monkeypatch.setattr("riftlift.library.add", add)
    code, events = _run_worker(monkeypatch, paths, "6323983201049540", 1)

    assert code == 0
    assert calls == ["6323983201049540"]
    assert [event["event"] for event in events] == ["finishing", "complete"]


def test_worker_installs_every_version_and_reports_failures_by_label(
    tmp_path, monkeypatch
) -> None:
    paths = _paths(tmp_path)
    builds = [
        Build("1", "Echo VR", "a", "34.4.1", 3),
        Build("1", "Echo VR", "b", "1.0", 2),
        Build("1", "Echo VR", "c", "0.9", 1),
    ]

    def add_all_versions(paths, url, *, on_build, on_finalizing):
        installed, failed = [], []
        for index, build in enumerate(builds, start=1):
            on_build(index, len(builds), build)
            if build.binary_id == "b":
                failed.append(
                    (build, NotLaunchableError(Path("/games/b"), ValueError("32-bit")))
                )
                continue
            if build.binary_id == "c":
                # Raw error text must never reach the UI: it can hold credentials.
                failed.append((build, RuntimeError("GET https://x?token=SECRET 404")))
                continue
            on_finalizing()
            installed.append(
                Game("echo-vr", "Echo VR", "1", "k", str(tmp_path), "e.exe", [])
            )
        return installed, failed

    monkeypatch.setattr("riftlift.library.add_all_versions", add_all_versions)
    code, events = _run_worker(monkeypatch, paths, "all", 1)

    assert code == 0
    assert [event["event"] for event in events] == [
        "version",
        "finishing",
        "version",
        "version",
        "failed",
        "failed",
        "complete",
    ]
    assert events[0] == {"event": "version", "index": 1, "total": 3}
    assert events[4] == {
        "event": "failed",
        "label": "1.0 (2)",
        "kind": "not_launchable",
        "detail": f"NotLaunchableError: downloaded to {Path('/games/b')}, "
        "but RiftLift cannot launch it: 32-bit",
    }
    assert events[5] == {
        "event": "failed",
        "label": "0.9 (1)",
        "kind": "failed",
        "detail": "RuntimeError: GET [url] 404",
    }
    assert "SECRET" not in json.dumps(events)


def test_worker_reports_an_error_when_no_version_installs(
    tmp_path, monkeypatch
) -> None:
    paths = _paths(tmp_path)
    build = Build("1", "Echo VR", "a", "1.0", 1)

    def add_all_versions(paths, url, *, on_build, on_finalizing):
        on_build(1, 1, build)
        return [], [(build, RuntimeError("HTTP 404"))]

    monkeypatch.setattr("riftlift.library.add_all_versions", add_all_versions)
    code, events = _run_worker(monkeypatch, paths, "all", 0)

    assert code == 1
    assert events[-1] == {
        "event": "error",
        "reason": "download_failed",
        "detail": "RuntimeError: no version could be installed",
    }


def test_job_sends_the_build_and_reads_version_events(tmp_path) -> None:
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    paths = _paths(tmp_path)
    plain = DownloadJob(paths, "1", False)
    assert "build" not in plain.request

    job = DownloadJob(paths, "1", False, build_selector="all")
    assert job.request["build"] == "all"
    versions = []
    job.version.connect(lambda index, total: versions.append((index, total)))
    job._finishing = True
    job._handle({"event": "version", "index": 2, "total": 3})
    job._handle({"event": "failed", "label": "1.0 (2)", "kind": "not_launchable"})
    job._handle(
        {
            "event": "failed",
            "label": "0.9 (1)",
            "kind": "anything else",
            "detail": "RuntimeError: HTTP 404",
        }
    )

    assert versions == [(2, 3)]
    assert job._finishing is False  # Pause works again between versions
    assert job.failed_versions == [("1.0 (2)", "not_launchable"), ("0.9 (1)", "failed")]
    assert job.failed_version_reasons == {"0.9 (1)": "RuntimeError: HTTP 404"}


def test_themed_notice_keeps_wrapped_text_visible() -> None:
    from riftlift import game_ui

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    text = "2 of 4 versions could not be downloaded:\n\n" + "\n".join(
        f"{index}.0.371193.0 (110{index}): downloaded, but RiftLift cannot launch "
        "it (often a 32-bit build)"
        for index in range(4)
    )
    dialog, layout = game_ui._themed_dialog(None, "Echo VR", text)
    layout.addWidget(QtWidgets.QPushButton("OK"))
    dialog.show()
    app.processEvents()
    label = dialog.findChild(QtWidgets.QLabel)

    assert label.height() >= label.heightForWidth(label.width())
    dialog.close()
    dialog.deleteLater()
