"""Windows taskbar identity and one library window per data directory."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from PySide6 import QtGui, QtNetwork

from .config import Paths


def activate_existing(app) -> bool:
    name = (
        "riftlift-"
        + hashlib.sha256(str(Paths.defaults().data).casefold().encode()).hexdigest()[
            :24
        ]
    )
    socket = QtNetwork.QLocalSocket()
    socket.connectToServer(name)
    if socket.waitForConnected(300):
        socket.write(b"activate")
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return True
    server = QtNetwork.QLocalServer(app)
    server.setSocketOptions(QtNetwork.QLocalServer.UserAccessOption)
    QtNetwork.QLocalServer.removeServer(name)
    if not server.listen(name):
        raise OSError("Could not open RiftLift's local application connection")

    def activate():
        while server.hasPendingConnections():
            client = server.nextPendingConnection()
            client.close()
            client.deleteLater()
        window = getattr(app, "_riftlift_window", None)
        if window:
            window.showNormal()
            window.raise_()
            window.activateWindow()
            app._riftlift_activations = getattr(app, "_riftlift_activations", 0) + 1

    server.newConnection.connect(activate)
    app._riftlift_server = server
    if getattr(sys, "frozen", False):
        icon = Path(sys._MEIPASS) / "assets/riftlift.ico"
        app.setWindowIcon(QtGui.QIcon(str(icon)))
    return False
