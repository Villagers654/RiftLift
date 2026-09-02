"""Small native Windows library; runtime execution stays in a child process."""

from __future__ import annotations

import sys

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .config import Paths, games
from .windows import add_local, doctor


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.paths = Paths.defaults()
        self.setWindowTitle("RiftLift — Windows experimental")
        self.resize(840, 580)
        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        layout.addWidget(QLabel("RiftLift native Windows host • x64 OpenXR / OpenVR"))
        layout.addWidget(
            QLabel(
                "Install games with Meta's PC app or the game's community installer, then add the .exe."
            )
        )
        self.library = QListWidget()
        layout.addWidget(self.library)
        row = QHBoxLayout()
        layout.addLayout(row)
        for label, action in [
            ("Add installed game…", self.add_game),
            ("Refresh", self.refresh),
            ("System", self.system),
        ]:
            button = QPushButton(label)
            button.clicked.connect(action)
            row.addWidget(button)
        self.backend = QComboBox()
        self.backend.addItems(["openxr", "openvr"])
        row.addWidget(self.backend)
        self.launch_button = QPushButton("Launch in VR")
        self.launch_button.clicked.connect(self.launch_game)
        row.addWidget(self.launch_button)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)
        self.child = QProcess(self)
        self.child.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.child.readyReadStandardOutput.connect(self.read_output)
        self.child.finished.connect(self.finished)
        self.child.errorOccurred.connect(self.process_error)
        self.refresh()
        self.system()

    def refresh(self):
        self.library.clear()
        try:
            for game in games(self.paths):
                item = QListWidgetItem(game.name)
                item.setData(Qt.ItemDataRole.UserRole, game.slug)
                self.library.addItem(item)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Library", str(error))

    def system(self):
        report, _ = doctor(self.paths)
        self.log.setPlainText(report)

    def add_game(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Select installed game", "", "Windows executable (*.exe)"
        )
        if filename:
            try:
                game = add_local(self.paths, filename)
                self.log.append(
                    f"Registered {game.name}; original game files unchanged."
                )
                self.refresh()
            except (OSError, ValueError, RuntimeError) as error:
                QMessageBox.critical(self, "Add game", str(error))

    def launch_game(self):
        item = self.library.currentItem()
        if not item or self.child.state() != QProcess.ProcessState.NotRunning:
            return
        self.launch_button.setEnabled(False)
        self.child.start(
            sys.executable,
            [
                "-m",
                "riftlift.cli",
                "launch",
                item.data(Qt.ItemDataRole.UserRole),
                "--backend",
                self.backend.currentText(),
            ],
        )

    def read_output(self):
        self.log.append(
            bytes(self.child.readAllStandardOutput()).decode("utf-8", errors="replace")
        )

    def finished(self, code, _status):
        self.launch_button.setEnabled(True)
        self.log.append(
            f"Process finished: {code}. Rendering requires an in-headset test."
        )

    def process_error(self, _error):
        self.launch_button.setEnabled(True)
        self.log.append(self.child.errorString())

    def closeEvent(self, event):
        if self.child.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(
                self, "Game running", "Close the game before closing RiftLift."
            )
            event.ignore()
        else:
            event.accept()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = Window()
    window.show()
    return app.exec()
