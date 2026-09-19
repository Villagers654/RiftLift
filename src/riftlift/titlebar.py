"""A custom, theme-matching title bar for RiftLift's frameless windows.

Qt's default window decoration is drawn by the desktop's window manager, so
no stylesheet can recolor it. Going frameless and drawing our own title bar
lets the whole app (main window and dialogs) look consistent, at the cost of
reimplementing drag-to-move, double-click-to-maximize, and edge resizing
ourselves - done here via `QWindow.startSystemMove`/`startSystemResize`,
which hand the actual interaction off to the compositor/window manager.
"""

from __future__ import annotations

from typing import ClassVar

from PySide6 import QtCore, QtGui, QtWidgets


class TitleBar(QtWidgets.QWidget):
    def __init__(
        self,
        window: QtWidgets.QWidget,
        title: str,
        *,
        minimizable: bool = False,
        maximizable: bool = False,
    ):
        super().__init__()
        self.setObjectName("titlebar")
        self.setFixedHeight(38)
        self._window = window

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 6, 0)
        layout.setSpacing(2)
        self.label = QtWidgets.QLabel(title)
        self.label.setObjectName("titlebar_label")
        layout.addWidget(self.label)
        layout.addStretch()

        if minimizable:
            layout.addWidget(self._button("-", window.showMinimized))
        self._maximize_button = None
        if maximizable:
            self._maximize_button = self._button("▢", self._toggle_maximize)
            layout.addWidget(self._maximize_button)
        layout.addWidget(self._button("✕", window.close, close=True))

    def set_title(self, text: str) -> None:
        self.label.setText(text)

    def _button(self, text: str, slot, *, close: bool = False) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setObjectName("titlebar_close" if close else "titlebar_button")
        button.setFixedSize(32, 28)
        button.setCursor(QtCore.Qt.ArrowCursor)
        button.clicked.connect(slot)
        return button

    def _toggle_maximize(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton and self._maximize_button is not None:
            self._toggle_maximize()
            return
        super().mouseDoubleClickEvent(event)


class ResizableFrame(QtWidgets.QWidget):
    """A frameless top-level widget's central widget: its own thin margin
    becomes a resize grip, since there's no native window border left."""

    MARGIN = 6
    _CURSORS: ClassVar[dict[tuple[bool, bool, bool, bool], QtCore.Qt.CursorShape]] = {
        (True, False, False, False): QtCore.Qt.SizeHorCursor,  # left
        (False, True, False, False): QtCore.Qt.SizeHorCursor,  # right
        (False, False, True, False): QtCore.Qt.SizeVerCursor,  # top
        (False, False, False, True): QtCore.Qt.SizeVerCursor,  # bottom
        (True, False, True, False): QtCore.Qt.SizeFDiagCursor,  # top-left
        (False, True, False, True): QtCore.Qt.SizeFDiagCursor,  # bottom-right
        (False, True, True, False): QtCore.Qt.SizeBDiagCursor,  # top-right
        (True, False, False, True): QtCore.Qt.SizeBDiagCursor,  # bottom-left
    }

    def __init__(self, window: QtWidgets.QWidget):
        super().__init__()
        self._window = window
        self.setMouseTracking(True)

    def _edges(self, pos: QtCore.QPoint) -> tuple[bool, bool, bool, bool]:
        rect = self.rect()
        left = pos.x() <= self.MARGIN
        right = pos.x() >= rect.width() - self.MARGIN
        top = pos.y() <= self.MARGIN
        bottom = pos.y() >= rect.height() - self.MARGIN
        return (left, right and not left, top, bottom and not top)

    @staticmethod
    def _qt_edges(edges: tuple[bool, bool, bool, bool]) -> QtCore.Qt.Edges:
        left, right, top, bottom = edges
        result = QtCore.Qt.Edges()
        if left:
            result |= QtCore.Qt.LeftEdge
        if right:
            result |= QtCore.Qt.RightEdge
        if top:
            result |= QtCore.Qt.TopEdge
        if bottom:
            result |= QtCore.Qt.BottomEdge
        return result

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        edges = self._edges(event.position().toPoint())
        cursor = self._CURSORS.get(edges)
        self.setCursor(cursor if cursor is not None else QtCore.Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        # Qt stops delivering mouseMoveEvent here as soon as the cursor
        # moves onto a child widget (titlebar, content, ...), so the resize
        # cursor set above would otherwise never get cleared again - every
        # child widget without its own cursor inherits whatever is set here.
        self.unsetCursor()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            edges = self._edges(event.position().toPoint())
            handle = self._window.windowHandle()
            if any(edges) and handle is not None:
                handle.startSystemResize(self._qt_edges(edges))
                return
        super().mousePressEvent(event)


def wrap_dialog(
    dialog: QtWidgets.QDialog,
    title: str,
    margins: tuple[int, int, int, int] = (26, 24, 26, 24),
) -> QtWidgets.QVBoxLayout:
    """Make a QDialog frameless with a custom title bar.

    Stores the title bar as `dialog.titlebar` (so callers can retitle it
    later) and returns the content layout callers should add widgets to,
    in place of the `QVBoxLayout(self)` pattern this replaces.
    """
    dialog.setWindowFlags(dialog.windowFlags() | QtCore.Qt.FramelessWindowHint)
    root = QtWidgets.QVBoxLayout(dialog)
    root.setContentsMargins(1, 1, 1, 1)
    root.setSpacing(0)
    dialog.titlebar = TitleBar(dialog, title)
    root.addWidget(dialog.titlebar)
    content = QtWidgets.QWidget()
    content_layout = QtWidgets.QVBoxLayout(content)
    content_layout.setContentsMargins(*margins)
    root.addWidget(content, 1)
    return content_layout
