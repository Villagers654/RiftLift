"""An owned download process; pausing never leaves a background download running."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict

from PySide6 import QtCore

from .config import Game, Paths
from .util import python_interpreter


class DownloadJob(QtCore.QObject):
    progress = QtCore.Signal(str, int, int)
    version = QtCore.Signal(int, int)
    finishing = QtCore.Signal()
    complete = QtCore.Signal(object, object)
    paused = QtCore.Signal()

    def __init__(
        self,
        paths: Paths,
        url: str,
        sync_steam: bool,
        parent=None,
        *,
        build_selector: str | None = None,
    ):
        super().__init__(parent)
        self.paths = paths
        self.request = {
            "paths": {key: str(value) for key, value in asdict(paths).items()},
            "url": url,
            "sync_steam": sync_steam,
        }
        if build_selector:
            # A version, version code or binary ID, or "all" for every build.
            self.request["build"] = build_selector
        # (version label, "not_launchable" | "failed") for every-version installs.
        self.failed_versions: list[tuple[str, str]] = []
        # Why each of them failed, by version label.
        self.failed_version_reasons: dict[str, str] = {}
        self.process = QtCore.QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(
            lambda: self.process.readAllStandardError()
        )
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.process.started.connect(self._send_request)
        self._buffer = b""
        self._result = None
        self._error = None
        self.error_detail = ""
        self._pause_requested = False
        self._terminal = False
        self._finishing = False
        self._warning = None

    def _command(self):
        if getattr(sys, "frozen", False):
            return sys.executable, ["--download-worker"]
        # The real interpreter, not an AppImage launcher or Windows venv
        # redirector, so QProcess owns the worker and its download threads.
        return python_interpreter(), [
            "-m",
            "riftlift.download_worker",
        ]

    def start(self):
        program, arguments = self._command()
        if not getattr(sys, "frozen", False):
            environment = QtCore.QProcessEnvironment.systemEnvironment()
            environment.insert("PYTHONPATH", os.pathsep.join(sys.path))
            environment.insert("PYTHONUNBUFFERED", "1")
            self.process.setProcessEnvironment(environment)
        self.process.start(program, arguments)

    def _send_request(self):
        self.process.write(json.dumps(self.request).encode("utf-8") + b"\n")
        if self._pause_requested:
            self.process.kill()

    def pause(self):
        self._read()
        if self._terminal or self._finishing:
            return
        self._pause_requested = True
        if self.process.state() != QtCore.QProcess.NotRunning:
            self.process.kill()

    def _begin_finalization(self):
        self._finishing = True
        self.finishing.emit()
        if self.process.state() == QtCore.QProcess.Running:
            self.process.write(b'{"event":"finalize"}\n')
            # An every-version install finalizes once per version.
            if "build" not in self.request:
                self.process.closeWriteChannel()

    def _read(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        if len(self._buffer) > 65536:
            self._error = "worker_failed"
            self.process.kill()
            return
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                self._handle(json.loads(line))
            except (ValueError, KeyError, TypeError):
                self._error = "worker_failed"
                self.process.kill()
                return

    def _handle(self, event):
        kind = event["event"]
        if kind == "progress":
            label = event["label"]
            if label in {
                "Preparing segments",
                "Downloading",
                "Assembling files",
            }:
                self.progress.emit(label, int(event["current"]), int(event["total"]))
        elif kind == "finishing":
            self._begin_finalization()
        elif kind in {"version", "failed"}:
            self._handle_version(kind, event)
        elif kind == "complete":
            self._result = Game.load(self.paths, event["slug"])
        elif kind == "error":
            self._error = (
                event["reason"]
                if event["reason"] in {"sign_in_required", "not_launchable"}
                else "download_failed"
            )
            self.error_detail = str(event.get("detail") or "")[:300]
        elif kind == "warning":
            self._warning = "steam_sync_failed"

    def _handle_version(self, kind, event):
        """Events of an install that downloads every version."""
        if kind == "version":
            # The previous version is saved; Pause is safe again.
            self._finishing = False
            self.version.emit(int(event["index"]), int(event["total"]))
            return
        failure = event["kind"]
        label = str(event["label"])[:80]
        self.failed_versions.append(
            (label, failure if failure == "not_launchable" else "failed")
        )
        if detail := str(event.get("detail") or "")[:300]:
            self.failed_version_reasons[label] = detail

    def _process_error(self, error):
        if error == QtCore.QProcess.FailedToStart:
            self._error = "worker_failed"
            self._finished(-1, QtCore.QProcess.NormalExit)

    def _finished(self, code, status):
        if self._terminal:
            return
        self._read()
        self._terminal = True
        if self._result is not None:
            self.complete.emit(self._result, self._warning)
        elif self._pause_requested:
            self.paused.emit()
        else:
            self.complete.emit(None, self._error or "worker_failed")
