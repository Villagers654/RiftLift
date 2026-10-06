"""Exercise real owned processes and the actual installer, without credentials."""

import io
import json
import os
import sys
import time
import uuid
from pathlib import Path

import pytest

if os.name != "nt":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtTest, QtWidgets

from riftlift.config import Paths
from riftlift.download_job import DownloadJob
from riftlift.game_ui import StoreGameDialog
from riftlift.i18n import set_language


@pytest.fixture(scope="session")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait(app, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        QtTest.QTest.qWait(10)
    return False


@pytest.fixture
def dialog(app, monkeypatch):
    set_language("en")
    root = Path(__file__).resolve().parents[3] / "review-fixtures" / str(uuid.uuid4())
    paths = Paths(
        *(root / key for key in ("data", "cache", "config", "games", "prefix", "tools"))
    )
    paths.create()
    script = Path(__file__).with_name("fixture_download.py")
    monkeypatch.setattr(
        DownloadJob,
        "_command",
        lambda self: (getattr(sys, "_base_executable", sys.executable), [str(script)]),
    )
    widget = StoreGameDialog(
        paths,
        lambda: None,
        initial_url="https://www.meta.com/experiences/pcvr/fixture/123456789/",
        simple_name="Fixture game",
    )
    widget.show()
    app.processEvents()
    yield widget
    for job in widget.findChildren(DownloadJob):
        if job.process.state() != QtCore.QProcess.NotRunning:
            job.process.kill()
            job.process.waitForFinished(2000)
    widget.close()
    widget.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def test_pause_stops_owned_worker_and_resume_keeps_completed_segment(app, dialog):
    dialog.submit.click()
    assert wait(app, lambda: dialog.progress.value() == 1)
    job = dialog._job
    assert job.process.state() == QtCore.QProcess.Running
    dialog.cancel_button.click()
    assert wait(app, lambda: not dialog._busy)
    assert job.process.state() == QtCore.QProcess.NotRunning
    segment = dialog.paths.cache / "verified-segment"
    assert segment.read_bytes() == b"fixture verified segment"
    assert dialog.submit.text() == "Resume"
    assert dialog.submit.hasFocus()
    QtTest.QTest.keyClick(dialog.submit, QtCore.Qt.Key_Space)
    assert wait(app, lambda: dialog.installed_game is not None)
    assert dialog.installed_game.slug == "fixture"
    assert segment.read_bytes() == b"fixture verified segment"


def test_escape_waits_for_actual_process_exit_before_closing(app, dialog):
    dialog.submit.click()
    assert wait(app, lambda: dialog.progress.value() == 1)
    job = dialog._job
    QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Escape)
    assert wait(app, lambda: not dialog.isVisible())
    assert job.process.state() == QtCore.QProcess.NotRunning
    assert not dialog._busy


@pytest.mark.parametrize(
    "reason", ["sign_in_required", "download_failed", "crash", "malformed"]
)
def test_failure_recovers_controls_and_keyboard_retry(app, dialog, reason):
    (dialog.paths.cache / "mode").write_text(reason)
    dialog.submit.click()
    assert wait(app, lambda: not dialog._busy)
    assert dialog.installed_game is None
    assert dialog.submit.text() == "Retry"
    assert dialog.submit.isEnabled() and dialog.submit.hasFocus()
    assert dialog.cancel_button.isEnabled()
    if reason == "sign_in_required":
        assert "Sign in again" in dialog.validation.text()
    (dialog.paths.cache / "mode").write_text("complete")
    QtTest.QTest.keyClick(dialog.submit, QtCore.Qt.Key_Space)
    assert wait(app, lambda: dialog.installed_game is not None)


def test_close_during_finalization_waits_and_preserves_install(app, dialog):
    (dialog.paths.cache / "mode").write_text("finishing")
    dialog.submit.click()
    assert wait(app, lambda: dialog._job._finishing)
    assert not dialog.cancel_button.isEnabled()
    dialog.close()
    app.processEvents()
    assert dialog.isVisible()
    (dialog.paths.cache / "release").write_text("ready")
    assert wait(app, lambda: dialog.installed_game is not None)
    assert dialog.result() == QtWidgets.QDialog.Accepted


def test_steam_sync_failure_keeps_installed_game_and_reports_warning(app, dialog):
    (dialog.paths.cache / "mode").write_text("warning")
    dialog.submit.click()
    assert wait(app, lambda: dialog.installed_game is not None)
    assert dialog.install_warning == "steam_sync_failed"


def test_worker_classifies_auth_failure_without_exposing_url(app, dialog, monkeypatch):
    from riftlift import download_worker

    def fail(*args):
        raise ValueError("401 https://fixture.invalid/?access_token=SECRET_FIXTURE")

    monkeypatch.setattr("riftlift.library.add", fail)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                dialog._job.request
                if dialog._job
                else {
                    "paths": {
                        key: str(getattr(dialog.paths, key))
                        for key in (
                            "data",
                            "cache",
                            "config",
                            "games",
                            "prefix",
                            "tools",
                        )
                    },
                    "url": dialog.entry.text(),
                    "sync_steam": False,
                }
            )
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    assert download_worker.main() == 1
    assert "SECRET_FIXTURE" not in output.getvalue()
    assert json.loads(output.getvalue())["reason"] == "sign_in_required"
