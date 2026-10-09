"""Windows taskbar identity, dark title bars and one library window per data directory."""

from __future__ import annotations

import ctypes
import hashlib
import sys
from pathlib import Path

from PySide6 import QtCore, QtGui, QtNetwork

from .config import Paths


class _DarkTitleBars(QtCore.QObject):
    """Match the native caption to RiftLift's dark theme on every top-level window."""

    def eventFilter(self, watched, event):
        if (
            event.type() == QtCore.QEvent.Show
            and getattr(watched, "isWindow", lambda: False)()
            and not watched.property("riftlift_dark_caption")
        ):
            watched.setProperty("riftlift_dark_caption", True)
            enabled = ctypes.c_int(1)
            # DWMWA_USE_IMMERSIVE_DARK_MODE; ignored by Windows builds without it.
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(int(watched.winId())),
                20,
                ctypes.byref(enabled),
                ctypes.sizeof(enabled),
            )
        return False


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
    app._riftlift_title_bars = _DarkTitleBars(app)
    app.installEventFilter(app._riftlift_title_bars)
    if getattr(sys, "frozen", False):
        icon = Path(sys._MEIPASS) / "assets/riftlift.ico"
        app.setWindowIcon(QtGui.QIcon(str(icon)))
    return False
