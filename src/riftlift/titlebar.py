"""Dialog layouts using the platform's native window controls."""

from PySide6 import QtCore, QtWidgets


def wrap_dialog(
    dialog: QtWidgets.QDialog,
    title: str,
    margins: tuple[int, int, int, int] = (26, 24, 26, 24),
) -> QtWidgets.QVBoxLayout:
    dialog.setWindowTitle(title)
    dialog.setWindowFlag(QtCore.Qt.FramelessWindowHint, False)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(*margins)
    return layout
