"""An owned download process; pausing never leaves a background download running."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict

from PySide6 import QtCore

from .config import Game, Paths


class DownloadJob(QtCore.QObject):
    progress = QtCore.Signal(str, int, int)
    finishing = QtCore.Signal()
    complete = QtCore.Signal(object, object)
    paused = QtCore.Signal()

    def __init__(self, paths: Paths, url: str, sync_steam: bool, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.request = {
            "paths": {key: str(value) for key, value in asdict(paths).items()},
            "url": url,
            "sync_steam": sync_steam,
        }
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
        self._pause_requested = False
        self._terminal = False
        self._finishing = False
        self._warning = None

    def _command(self):
        if getattr(sys, "frozen", False):
            return sys.executable, ["--download-worker"]
        # Windows venv python.exe is a redirector. Use the real interpreter
        # so QProcess owns the actual worker, including its download threads.
        return getattr(sys, "_base_executable", sys.executable), [
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
        self.process.closeWriteChannel()
        if self._pause_requested:
            self.process.kill()

    def pause(self):
        if self._terminal or self._finishing:
            return
        self._pause_requested = True
        if self.process.state() != QtCore.QProcess.NotRunning:
            self.process.kill()

    def _read(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        if len(self._buffer) > 65536:
            self._error = "worker_failed"
            self.process.kill()
            return
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                event = json.loads(line)
                kind = event["event"]
                if kind == "progress":
                    label = event["label"]
                    if label in {
                        "Preparing segments",
                        "Downloading",
                        "Assembling files",
                    }:
                        self.progress.emit(
                            label, int(event["current"]), int(event["total"])
                        )
                elif kind == "finishing":
                    self._finishing = True
                    self.finishing.emit()
                elif kind == "complete":
                    self._result = Game.load(self.paths, event["slug"])
                elif kind == "error":
                    self._error = (
                        event["reason"]
                        if event["reason"] == "sign_in_required"
                        else "download_failed"
                    )
                elif kind == "warning":
                    self._warning = "steam_sync_failed"
            except (ValueError, KeyError, TypeError):
                self._error = "worker_failed"
                self.process.kill()
                return

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
