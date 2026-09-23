"""RiftLift's primary desktop window."""

from __future__ import annotations

import contextlib
import io
import shutil
import threading
from collections.abc import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from .auth import is_signed_in, runtime_access_token
from .auth_ui import AuthDialog
from .config import Game, Paths, games, language_preference, set_language_preference
from .doctor import doctor
from .doctor_components import needs_setup
from .entitlements import OwnedApp, list_owned_pcvr_apps
from .game_ui import LaunchOptionsDialog, LocalGameDialog, StoreGameDialog
from .i18n import LANGUAGES, current_language, namespace, set_language
from .launch import launch
from .library import add_local, remove
from .metadata import (
    fetch_catalog_metadata,
    fetch_owned_hero,
    fetch_owned_icon,
    fetch_owned_metadata,
    fetch_steam_catalog_metadata,
    populate_game_metadata,
)
from .pages.settings import SettingsPage
from .playtime import format_playtime, playtime
from .runtime import setup
from .steam import sync_with_restart
from .steam_oculus import add_steam_game
from .steam_ui import SteamGamesDialog
from .theme import STYLE
from .titlebar import ResizableFrame, TitleBar, wrap_dialog
from .util import RiftLiftError
from .xr_runtime import active_runtime_json

APP = namespace("app")
SETUP = namespace("setup")
ACTION = namespace("action")
NAV = namespace("nav")
LIBRARY = namespace("library")
EMPTY = namespace("empty")
GAME = namespace("game")
STATUS = namespace("status")
ACTIVITY = namespace("activity")
CONFIRM = namespace("confirm")
TASK = namespace("task")
LAUNCH_OPTIONS = namespace("launch_options")


def _playtime_text(value) -> str:
    if value.launches == 0:
        return GAME("not_played_yet")
    return GAME("played_for").format(duration=format_playtime(value.seconds))


def _short_description(text: str, limit: int = 320) -> str:
    paragraph = text.strip().split("\n\n", 1)[0].strip()
    if len(paragraph) <= limit:
        return paragraph
    return paragraph[:limit].rsplit(" ", 1)[0] + "…"


def _themed_dialog(
    parent, title: str, text: str
) -> tuple[QtWidgets.QDialog, QtWidgets.QVBoxLayout]:
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setStyleSheet(STYLE)
    dialog.setMinimumWidth(420)
    layout = wrap_dialog(dialog, title, margins=(24, 22, 24, 22))
    layout.setSpacing(14)
    label = QtWidgets.QLabel(text)
    label.setWordWrap(True)
    layout.addWidget(label)
    return dialog, layout


def _themed_question(parent, title: str, text: str) -> bool:
    dialog, layout = _themed_dialog(parent, title, text)
    buttons = QtWidgets.QHBoxLayout()
    buttons.addStretch()
    no_button = QtWidgets.QPushButton(ACTION("no"))
    no_button.clicked.connect(dialog.reject)
    buttons.addWidget(no_button)
    yes_button = QtWidgets.QPushButton(ACTION("yes"))
    yes_button.setObjectName("primary")
    yes_button.clicked.connect(dialog.accept)
    buttons.addWidget(yes_button)
    layout.addLayout(buttons)
    return dialog.exec() == QtWidgets.QDialog.Accepted


def _themed_error(parent, title: str, text: str) -> None:
    dialog, layout = _themed_dialog(parent, title, text)
    ok_button = QtWidgets.QPushButton(ACTION("ok"))
    ok_button.setObjectName("primary")
    ok_button.clicked.connect(dialog.accept)
    layout.addWidget(ok_button, alignment=QtCore.Qt.AlignRight)
    dialog.exec()


class Events(QtCore.QObject):
    output = QtCore.Signal(str)
    complete = QtCore.Signal(str, object, object, object)


class OwnedEvents(QtCore.QObject):
    complete = QtCore.Signal(object, object)


class OwnedDetailEvents(QtCore.QObject):
    complete = QtCore.Signal(int, str, object, object)


class GameMetadataEvents(QtCore.QObject):
    complete = QtCore.Signal(int, str, object, object)


class SetupStatusEvents(QtCore.QObject):
    complete = QtCore.Signal(bool)


class SystemStatusEvents(QtCore.QObject):
    complete = QtCore.Signal(bool, str)


class Output(io.TextIOBase):
    def __init__(self, emit: Callable[[str], None]):
        self.emit = emit

    def write(self, value: str) -> int:
        if value:
            self.emit(value)
        return len(value)

    def flush(self) -> None:
        pass


class SkeletonLines(QtWidgets.QWidget):
    """A pulsing placeholder for text that hasn't loaded yet."""

    _DEFAULT_WIDTHS = (1.0, 0.94, 0.62)
    _BAR_HEIGHT = 12
    _BAR_SPACING = 10

    def __init__(self, widths: tuple[float, ...] | None = None):
        super().__init__()
        self._widths = widths or self._DEFAULT_WIDTHS
        self.setFixedHeight(
            len(self._widths) * self._BAR_HEIGHT
            + (len(self._widths) - 1) * self._BAR_SPACING
        )
        self._effect = QtWidgets.QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._animation = QtCore.QPropertyAnimation(self._effect, b"opacity", self)
        self._animation.setStartValue(0.35)
        self._animation.setEndValue(0.85)
        self._animation.setDuration(900)
        self._animation.setEasingCurve(QtCore.QEasingCurve.InOutSine)
        self._animation.setLoopCount(-1)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        self._animation.start()
        super().showEvent(event)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        if self._animation.state() != QtCore.QAbstractAnimation.Stopped:
            self._animation.pause()
        super().hideEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#1c2740"))
        y = 0
        for width in self._widths:
            painter.drawRoundedRect(
                QtCore.QRectF(0, y, self.width() * width, self._BAR_HEIGHT), 5, 5
            )
            y += self._BAR_HEIGHT + self._BAR_SPACING


class HeroPanel(QtWidgets.QWidget):
    """Selected-game artwork with a readable, content-first text area."""

    def __init__(self):
        super().__init__()
        self.hero = QtGui.QPixmap()

    def set_artwork(self, path: str) -> None:
        self.hero = QtGui.QPixmap(path)
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        clip = QtGui.QPainterPath()
        clip.addRoundedRect(QtCore.QRectF(self.rect()), 10, 10)
        painter.setClipPath(clip)
        painter.fillRect(self.rect(), QtGui.QColor("#0b1020"))
        if not self.hero.isNull():
            artwork = self.hero.scaled(
                self.size(),
                QtCore.Qt.KeepAspectRatioByExpanding,
                QtCore.Qt.SmoothTransformation,
            )
            source = QtCore.QRect(
                (artwork.width() - self.width()) // 2,
                (artwork.height() - self.height()) // 2,
                self.width(),
                self.height(),
            )
            painter.drawPixmap(self.rect(), artwork, source)

        # The art now spans the full banner, so the overlay has to stay dark
        # enough everywhere for the title/actions to read over any artwork.
        horizontal = QtGui.QLinearGradient(0, 0, self.width(), 0)
        horizontal.setColorAt(0.0, QtGui.QColor(11, 16, 32, 235))
        horizontal.setColorAt(0.6, QtGui.QColor(11, 16, 32, 190))
        horizontal.setColorAt(1.0, QtGui.QColor(11, 16, 32, 90))
        painter.fillRect(self.rect(), horizontal)

        vertical = QtGui.QLinearGradient(0, self.height() * 0.3, 0, self.height())
        vertical.setColorAt(0.0, QtGui.QColor(11, 16, 32, 0))
        vertical.setColorAt(1.0, QtGui.QColor(11, 16, 32, 245))
        painter.fillRect(self.rect(), vertical)


class Window(QtWidgets.QMainWindow):
    def __init__(
        self,
        paths: Paths | None = None,
        *,
        initial_slug: str | None = None,
        initial_owned_app_id: str | None = None,
    ):
        super().__init__()
        self.paths = paths or Paths.defaults()
        set_language(language_preference(self.paths))
        self.installed: list[Game] = []
        self.owned: list[OwnedApp] = []
        self.selected_owned: OwnedApp | None = None
        self.slug: str | None = initial_slug
        self._pending_owned_app_id = initial_owned_app_id
        self._owned_loaded = False
        self.busy = False
        self.busy_label = ""
        self.log = ""
        self.log_views: list[QtWidgets.QTextEdit] = []
        self.events = Events()
        self.events.output.connect(self._append_log)
        self.events.complete.connect(self._finish)
        self.owned_events = OwnedEvents()
        self.owned_events.complete.connect(self._finish_owned_scan)
        self.owned_detail_events = OwnedDetailEvents()
        self.owned_detail_events.complete.connect(self._finish_owned_detail)
        self.game_metadata_events = GameMetadataEvents()
        self.game_metadata_events.complete.connect(self._finish_game_metadata_refresh)
        self.setup_status_events = SetupStatusEvents()
        self.setup_status_events.complete.connect(self._finish_setup_check)
        self.system_status_events = SystemStatusEvents()
        self.system_status_events.complete.connect(self._finish_system_status_check)
        self.setWindowTitle(APP("name"))
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.FramelessWindowHint)
        self.resize(1024, 637)
        self.setMinimumSize(1024, 637)
        self.setStyleSheet(STYLE)
        self._build()
        self.refresh()
        if needs_setup(self.paths):
            self.run_task(
                "Setting up the compatibility runtime",
                lambda: setup(self.paths),
                success="Compatibility runtime is ready",
            )
        else:
            self._check_setup_status()
        self.refresh_owned()

    def label(self, text="", name=""):
        widget = QtWidgets.QLabel(text)
        widget.setObjectName(name)
        return widget

    def button(self, text, callback, primary=False):
        widget = QtWidgets.QPushButton(text)
        widget.setObjectName("primary" if primary else "")
        widget.clicked.connect(callback)
        return widget

    def _build_header(self, outer: QtWidgets.QVBoxLayout) -> None:
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(self.label(APP("name"), "title"))
        header.addStretch()
        self.settings_button = self.button(NAV("settings"), self._toggle_settings)
        self.settings_button.setObjectName("nav")
        self.signin = self.button(
            NAV("account") if is_signed_in(self.paths) else NAV("sign_in"),
            self.show_auth,
        )
        self.signin.setObjectName("nav")
        self.steam_games = self.button(NAV("steam_games"), self.steam_dialog)
        self.steam_games.setObjectName("nav")
        self.addbtn = self.button(NAV("add_game"), lambda: self.add_dialog(), True)
        for button in (
            self.settings_button,
            self.signin,
            self.steam_games,
            self.addbtn,
        ):
            header.addWidget(button)
        outer.addLayout(header)
        outer.addSpacing(14)

    def _build_setup_banner(self, outer: QtWidgets.QVBoxLayout) -> None:
        # Wrapped in its own container (rather than a separate outer.addSpacing)
        # so hiding it also removes the gap below it - otherwise the header
        # area stays taller than necessary even while there's nothing to show.
        container = QtWidgets.QWidget()
        self.setup_banner = container
        self.setup_banner.hide()
        container_layout = QtWidgets.QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 14)
        banner = QtWidgets.QWidget()
        banner.setObjectName("setup_banner")
        layout = QtWidgets.QHBoxLayout(banner)
        layout.setContentsMargins(14, 10, 14, 10)
        text = self.label(SETUP("banner_text"), "setup_banner_text")
        text.setWordWrap(True)
        layout.addWidget(text, 1)
        run_now = self.button(SETUP("run_now"), self._run_setup, True)
        layout.addWidget(run_now)
        container_layout.addWidget(banner)
        outer.addWidget(container)

    def _check_setup_status(self) -> None:
        def worker():
            self.setup_status_events.complete.emit(needs_setup(self.paths))

        threading.Thread(
            target=worker, daemon=True, name="riftlift-setup-check"
        ).start()

    def _finish_setup_check(self, needed: bool) -> None:
        self.setup_banner.setVisible(needed)

    def _run_setup(self) -> None:
        self.run_task(SETUP("running"), lambda: setup(self.paths), SETUP("done"))

    def _check_system_status(self) -> None:
        def worker():
            if needs_setup(self.paths):
                self.system_status_events.complete.emit(
                    False, SETUP("status_needs_setup")
                )
                return
            try:
                active_runtime_json()
            except RiftLiftError:
                self.system_status_events.complete.emit(
                    False, SETUP("status_no_openxr_runtime")
                )
                return
            self.system_status_events.complete.emit(True, SETUP("status_ok"))

        threading.Thread(
            target=worker, daemon=True, name="riftlift-status-check"
        ).start()

    def _finish_system_status_check(self, healthy: bool, message: str) -> None:
        self.settings_page.set_status(healthy, message)

    def _toggle_settings(self):
        if self.view_stack.currentIndex() == 1:
            self._leave_settings()
        else:
            self.show_settings()

    def show_settings(self):
        self.view_stack.setCurrentIndex(1)
        self.settings_button.setText(LIBRARY("title"))
        self._check_system_status()

    def _leave_settings(self):
        self.view_stack.setCurrentIndex(0)
        self.settings_button.setText(NAV("settings"))

    def _confirm_language_change(self, code: str) -> bool:
        if self.busy:
            _themed_error(self, APP("name"), STATUS("busy"))
            return False
        if not _themed_question(
            self,
            APP("name"),
            CONFIRM("change_language").format(language=LANGUAGES[code]),
        ):
            return False
        set_language_preference(self.paths, code)
        shutil.rmtree(self.paths.cache / "owned-metadata", ignore_errors=True)
        self._restart()
        return True

    def _restart(self) -> None:
        new_window = Window(
            self.paths,
            initial_slug=self.slug,
            initial_owned_app_id=(
                self.selected_owned.app_id if self.selected_owned else None
            ),
        )
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app._riftlift_window = new_window
        new_window.show()
        self.close()

    def _run_system_check(self) -> None:
        self.run_task(TASK("checking_system"), lambda: doctor(self.paths))

    def _build_left_column(self) -> QtWidgets.QWidget:
        column = QtWidgets.QWidget()
        column.setFixedWidth(280)
        layout = QtWidgets.QVBoxLayout(column)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(12)
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(self.label(LIBRARY("title"), "section"))
        heading.addStretch()
        self.refresh_button = self.button("⟳", self.refresh_all)
        self.refresh_button.setObjectName("refresh")
        self.refresh_button.setToolTip(LIBRARY("refresh_tooltip"))
        self.refresh_button.setFixedSize(34, 34)
        heading.addWidget(self.refresh_button)
        layout.addLayout(heading)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QtCore.QSize(40, 40))
        self.tree.setIndentation(0)
        self.tree.setRootIsDecorated(False)
        self.tree.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.tree.currentItemChanged.connect(self._tree_item_changed)
        self.tree.itemClicked.connect(self._category_clicked)
        self.tree.itemExpanded.connect(self._category_toggled)
        self.tree.itemCollapsed.connect(self._category_toggled)
        self._categories: list[tuple[QtWidgets.QTreeWidgetItem, str]] = []
        self._installed_category = self._add_category("installed")
        self._steam_category = self._add_category("installed_steam")
        self._owned_category = self._add_category("not_installed")
        self.tree.expandAll()
        layout.addWidget(self.tree, 1)
        return column

    def _add_category(self, label_key: str) -> QtWidgets.QTreeWidgetItem:
        item = QtWidgets.QTreeWidgetItem([""])
        item.setFlags(QtCore.Qt.ItemIsEnabled)
        self.tree.addTopLevelItem(item)
        self._categories.append((item, label_key))
        return item

    def _category_clicked(self, item, _column):
        if item.childCount() and any(
            item is category for category, _ in self._categories
        ):
            item.setExpanded(not item.isExpanded())

    def _category_toggled(self, item):
        for category, label_key in self._categories:
            if item is category:
                self._set_category_text(item, LIBRARY(label_key))
                return

    @staticmethod
    def _set_category_text(item: QtWidgets.QTreeWidgetItem, label: str) -> None:
        count = item.childCount()
        item.setHidden(count == 0)
        arrow = "▾" if item.isExpanded() else "▸"
        item.setText(0, f"{arrow} {label} ({count})")

    def _build_empty_state(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.addStretch()
        title = self.label(EMPTY("title"), "game")
        title.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(title)
        hint = self.label(
            EMPTY("hint"),
            "muted",
        )
        hint.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(hint)
        layout.addWidget(
            self.button(NAV("add_game"), lambda: self.add_dialog(), True),
            alignment=QtCore.Qt.AlignCenter,
        )
        layout.addStretch()
        return panel

    def _build_game_detail(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.hero = HeroPanel()
        self.hero.setObjectName("detail")
        self.hero.setFixedHeight(300)
        layout = QtWidgets.QVBoxLayout(self.hero)
        layout.setContentsMargins(24, 24, 24, 24)
        info = QtWidgets.QVBoxLayout()
        info.setSpacing(0)
        info.addStretch()
        self.game_name = self.label("", "game")
        self.game_name.setWordWrap(True)
        info.addWidget(self.game_name)
        info.addSpacing(8)
        self.meta_row = QtWidgets.QHBoxLayout()
        self.meta_row.setSpacing(8)
        self.meta = self.label("", "muted")
        self.meta.setWordWrap(True)
        self.meta_row.addWidget(self.meta)
        self.meta_detail = self.label("", "muted")
        self.meta_detail.setWordWrap(True)
        self.meta_row.addWidget(self.meta_detail)
        self.meta_skeleton = SkeletonLines(widths=(0.5,))
        self.meta_skeleton.setFixedWidth(130)
        self.meta_skeleton.hide()
        self.meta_row.addWidget(self.meta_skeleton)
        info.addLayout(self.meta_row)
        info.addSpacing(14)
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(10)
        self.launch = self.button(GAME("launch"), self.launch_game, True)
        actions.addWidget(self.launch)
        self.install_here = self.button(
            GAME("install"), lambda: self.install_owned(), True
        )
        actions.addWidget(self.install_here)
        self.files_button = self.button(GAME("files"), self.open_folder)
        actions.addWidget(self.files_button)
        actions.addWidget(self.button(GAME("launch_options"), self.launch_options))
        self.add_steam_button = self.button(
            GAME("add_to_steam"), self.add_selected_to_steam
        )
        actions.addWidget(self.add_steam_button)
        self.uninstall_button = self.button(GAME("uninstall"), self.uninstall_selected)
        self.uninstall_button.setObjectName("danger")
        actions.addWidget(self.uninstall_button)
        actions.addStretch()
        info.addLayout(actions)
        info.addSpacing(10)
        store_link_row = QtWidgets.QHBoxLayout()
        self.store_link = self.button(GAME("open_rift_store"), self.open_store)
        self.store_link.setObjectName("store_link")
        self.store_link.setCursor(QtCore.Qt.PointingHandCursor)
        store_link_row.addWidget(self.store_link)
        store_link_row.addStretch()
        info.addLayout(store_link_row)
        layout.addLayout(info)
        outer.addWidget(self.hero)

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(24, 8, 24, 20)
        body_layout.setSpacing(8)
        body_layout.setAlignment(QtCore.Qt.AlignTop)
        self.description_heading = self.label(GAME("about"), "section")
        body_layout.addWidget(self.description_heading)
        self.description_label = self.label("", "description")
        self.description_label.setWordWrap(True)
        self.description_label.setMaximumWidth(760)
        body_layout.addWidget(self.description_label)
        self.description_skeleton = SkeletonLines()
        self.description_skeleton.setMaximumWidth(760)
        self.description_skeleton.hide()
        body_layout.addWidget(self.description_skeleton)
        body_layout.addStretch()
        outer.addWidget(body, 1)

        return page

    def _build_status_bar(self, outer: QtWidgets.QVBoxLayout) -> None:
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet("color:#263552")
        outer.addWidget(line)
        outer.addSpacing(20)
        bar = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status = self.label(STATUS("ready"), "muted")
        layout.addWidget(self.status, 1)
        activity = self.button(STATUS("view_activity"), self.show_activity)
        activity.setObjectName("nav")
        layout.addWidget(activity)
        outer.addWidget(bar)

    def _build(self):
        root = ResizableFrame(self)
        self.setCentralWidget(root)
        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setContentsMargins(
            ResizableFrame.MARGIN,
            ResizableFrame.MARGIN,
            ResizableFrame.MARGIN,
            ResizableFrame.MARGIN,
        )
        root_layout.setSpacing(0)
        self.titlebar = TitleBar(self, APP("name"), minimizable=True, maximizable=True)
        self.titlebar.setCursor(QtCore.Qt.ArrowCursor)
        root_layout.addWidget(self.titlebar)

        body = QtWidgets.QWidget()
        # Every child of the resizable central widget needs its own explicit
        # cursor, otherwise it inherits whatever ResizableFrame last set
        # while the mouse was hovering its resize margin (which only that
        # margin's mouseMoveEvent ever updates again).
        body.setCursor(QtCore.Qt.ArrowCursor)
        outer = QtWidgets.QVBoxLayout(body)
        outer.setContentsMargins(20, 17, 20, 15)
        outer.setSpacing(0)
        self._build_header(outer)
        self._build_setup_banner(outer)

        self.view_stack = QtWidgets.QStackedWidget()
        outer.addWidget(self.view_stack, 1)

        main_view = QtWidgets.QWidget()
        content = QtWidgets.QHBoxLayout(main_view)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(20)
        content.addWidget(self._build_left_column())
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.stack = QtWidgets.QStackedWidget()
        # The setup banner (and small windows in general) can leave less
        # height than the hero banner + description need; scroll instead of
        # letting them overlap.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.stack)
        rl.addWidget(scroll)
        content.addWidget(right, 1)
        self.stack.addWidget(self._build_empty_state())
        self.detail = self._build_game_detail()
        self.stack.addWidget(self.detail)
        self.view_stack.addWidget(main_view)

        self.settings_page = SettingsPage(
            self.paths,
            self._confirm_language_change,
            self._run_system_check,
            self._run_setup,
            self._check_system_status,
        )
        settings_scroll = QtWidgets.QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        settings_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        settings_scroll.setWidget(self.settings_page)
        self.view_stack.addWidget(settings_scroll)

        self._build_status_bar(outer)
        root_layout.addWidget(body, 1)

    def refresh(self, preferred=None):
        if preferred:
            self.slug = preferred
        self.installed = [g for g in games(self.paths) if g.executable_path.is_file()]
        self._render_tree()

    def refresh_all(self):
        self.refresh_library()
        self.refresh_owned()
        if self.selected_owned is not None:
            self.show_owned(self.selected_owned, refresh=True)

    def show_auth(self):
        dialog = AuthDialog(self.paths, self)
        if dialog.exec() == QtWidgets.QDialog.Accepted and dialog.completed:
            self.status.setText(STATUS("signed_in"))
            self.refresh_owned()
        elif not is_signed_in(self.paths):
            self.status.setText(STATUS("signed_out"))
        self.signin.setText(
            NAV("account") if is_signed_in(self.paths) else NAV("sign_in")
        )

    def steam_dialog(self):
        dialog = SteamGamesDialog(self.paths, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted or dialog.selected_game is None:
            return
        selected = dialog.selected_game

        def operation():
            game = add_steam_game(self.paths, selected)
            try:
                populate_game_metadata(self.paths, game, refresh=True)
            except RiftLiftError as error:
                print(f"warning: Steam catalog metadata was not available: {error}")
            return game.slug

        self.run_task(
            TASK("adding_from_steam").format(name=selected.name),
            operation,
            TASK("added_from_steam").format(name=selected.name),
            refresh=True,
        )

    def _tree_item_changed(self, current, _previous):
        if current is None:
            return
        data = current.data(0, QtCore.Qt.UserRole)
        if isinstance(data, Game):
            self.show_game(data)
        elif isinstance(data, OwnedApp):
            self.show_owned(data)

    def _find_item(self, category, predicate):
        for i in range(category.childCount()):
            child = category.child(i)
            if predicate(child.data(0, QtCore.Qt.UserRole)):
                return child
        return None

    def _build_game_item(self, game: Game) -> QtWidgets.QTreeWidgetItem:
        item = QtWidgets.QTreeWidgetItem([game.name])
        version_line = (
            "\n" + LIBRARY("version").format(version=game.version)
            if game.version
            else ""
        )
        item.setToolTip(0, game.name + version_line)
        item.setData(0, QtCore.Qt.UserRole, game)
        icon = QtGui.QIcon(game.artwork.get("icon", ""))
        if not icon.isNull():
            item.setIcon(0, icon)
        return item

    def _render_tree(self):
        self.tree.blockSignals(True)
        self._installed_category.takeChildren()
        self._steam_category.takeChildren()
        for game in self.installed:
            category = (
                self._steam_category
                if game.source == "steam"
                else self._installed_category
            )
            category.addChild(self._build_game_item(game))
        self._set_category_text(self._installed_category, LIBRARY("installed"))
        self._set_category_text(self._steam_category, LIBRARY("installed_steam"))

        installed_ids = {g.app_id for g in self.installed}
        not_installed = [app for app in self.owned if app.app_id not in installed_ids]
        icons = getattr(self, "_owned_icons", {})
        self._owned_category.takeChildren()
        for app in not_installed:
            item = QtWidgets.QTreeWidgetItem([app.name])
            item.setData(0, QtCore.Qt.UserRole, app)
            icon_path = icons.get(app.app_id)
            if icon_path:
                item.setIcon(0, QtGui.QIcon(icon_path))
            self._owned_category.addChild(item)
        self._set_category_text(self._owned_category, LIBRARY("not_installed"))
        self.tree.blockSignals(False)
        self._reselect()

    def _find_wanted_item(self) -> QtWidgets.QTreeWidgetItem | None:
        if self.slug:
            for category in (self._installed_category, self._steam_category):
                target = self._find_item(
                    category, lambda g: isinstance(g, Game) and g.slug == self.slug
                )
                if target is not None:
                    return target
        wanted_owned_id = (
            self.selected_owned.app_id
            if self.selected_owned
            else self._pending_owned_app_id
        )
        if wanted_owned_id:
            return self._find_item(
                self._owned_category,
                lambda a: isinstance(a, OwnedApp) and a.app_id == wanted_owned_id,
            )
        return None

    def _first_available_item(self) -> QtWidgets.QTreeWidgetItem | None:
        for category in (
            self._installed_category,
            self._steam_category,
            self._owned_category,
        ):
            if category.childCount():
                return category.child(0)
        return None

    def _reselect(self):
        target = self._find_wanted_item()
        wants_owned = bool(self.selected_owned or self._pending_owned_app_id)
        if target is None and wants_owned and not self._owned_loaded:
            # Still waiting on the owned-apps fetch; don't steal the
            # selection to something else before it's had a chance to land.
            return
        if target is None:
            target = self._first_available_item()
        if target is None:
            self.slug = None
            self.selected_owned = None
            self.stack.setCurrentIndex(0)
            return
        self.tree.blockSignals(True)
        self.tree.setCurrentItem(target)
        self.tree.blockSignals(False)
        data = target.data(0, QtCore.Qt.UserRole)
        if isinstance(data, Game):
            self.show_game(data)
        else:
            self.show_owned(data)

    def show_game(self, game):
        self.slug = game.slug
        self.selected_owned = None
        self.stack.setCurrentIndex(1)
        self.game_name.setText(game.name)
        self.meta_detail.hide()
        self.meta_skeleton.hide()
        self.meta_row.setStretchFactor(self.meta, 1)
        self.meta_row.setStretchFactor(self.meta_detail, 0)
        self.launch.setVisible(True)
        self.install_here.setVisible(False)
        self.files_button.setVisible(True)
        self.uninstall_button.setVisible(True)
        self.uninstall_button.setText(
            GAME("uninstall") if game.source == "meta" else GAME("remove_from_riftlift")
        )
        self.add_steam_button.setVisible(game.source != "steam")
        self.store_link.setVisible(game.source != "local")
        self.store_link.setText(
            GAME("open_steam") if game.source == "steam" else GAME("open_rift_store")
        )
        details = [
            value
            for value in (game.developer, game.version, ", ".join(game.genres[:2]))
            if value
        ]
        if not details:
            fallback = {
                "local": GAME("local"),
                "steam": f"Steam app {game.steam_app_id or game.app_id}",
                "meta": f"Meta app {game.app_id}",
            }
            details.append(fallback[game.source])
        details.append(_playtime_text(playtime(self.paths, game.slug)))
        self.meta.setText(" • ".join(details))
        self.hero.set_artwork(game.artwork.get("hero", ""))
        self._set_description(game.description)
        if game.source != "local" and game.description_lang != current_language():
            self._refresh_game_description(game)

    def _refresh_game_description(self, game: Game) -> None:
        if getattr(self, "_pending_description_refresh", None) == game.slug:
            # Already fetching this game's metadata (e.g. the owned-apps
            # scan re-triggered a reselect while the first fetch was still
            # in flight) - don't start a redundant, racing second fetch.
            return
        self._pending_description_refresh = game.slug
        token = self._generation_game_metadata = (
            getattr(self, "_generation_game_metadata", 0) + 1
        )

        def worker():
            try:
                is_steam = game.source == "steam"
                app_id = (
                    str(game.steam_app_id or game.app_id) if is_steam else game.app_id
                )
                metadata = (
                    fetch_steam_catalog_metadata(app_id)
                    if is_steam
                    else fetch_catalog_metadata(app_id)
                )
                self.game_metadata_events.complete.emit(
                    token, game.slug, metadata, None
                )
            except Exception as error:
                self.game_metadata_events.complete.emit(token, game.slug, None, error)

        threading.Thread(
            target=worker, daemon=True, name="riftlift-description-refresh"
        ).start()

    def _finish_game_metadata_refresh(self, token, slug, metadata, error):
        if getattr(self, "_pending_description_refresh", None) == slug:
            self._pending_description_refresh = None
        if token != getattr(self, "_generation_game_metadata", 0) or self.slug != slug:
            return
        if error is not None or metadata is None:
            self._append_log(
                f"\nCould not refresh {slug}'s description/genres: {error}\n"
            )
            return
        game = self.game()
        if game is None:
            return
        game.description = metadata.description
        game.developer = metadata.developer
        game.publisher = metadata.publisher
        game.genres = metadata.genres
        game.description_lang = current_language()
        game.save(self.paths)
        self.show_game(game)

    def show_owned(self, app: OwnedApp, *, refresh: bool = False):
        self.slug = None
        self.selected_owned = app
        self.stack.setCurrentIndex(1)
        self.game_name.setText(app.name)
        self.meta_row.setStretchFactor(self.meta, 0)
        self.meta_row.setStretchFactor(self.meta_detail, 1)
        if not refresh:
            self.meta.setText(GAME("not_installed"))
            self.meta_detail.hide()
            self.meta_skeleton.show()
            self.hero.set_artwork("")
            self._show_description_loading()
        self.launch.setVisible(False)
        self.install_here.setVisible(True)
        self.files_button.setVisible(False)
        self.uninstall_button.setVisible(False)
        self.add_steam_button.setVisible(False)
        self.store_link.setVisible(True)
        self.store_link.setText(GAME("open_rift_store"))

        token = self._generation_owned_detail = (
            getattr(self, "_generation_owned_detail", 0) + 1
        )

        def worker():
            try:
                metadata = fetch_owned_metadata(self.paths, app.app_id, refresh=refresh)
                hero = fetch_owned_hero(self.paths, app.app_id, refresh=refresh)
                self.owned_detail_events.complete.emit(
                    token, app.app_id, (metadata, hero), None
                )
            except Exception as error:
                self.owned_detail_events.complete.emit(token, app.app_id, None, error)

        threading.Thread(
            target=worker, daemon=True, name="riftlift-owned-detail"
        ).start()

    def _finish_owned_detail(self, token, app_id, result, error):
        if (
            token != getattr(self, "_generation_owned_detail", 0)
            or self.selected_owned is None
            or self.selected_owned.app_id != app_id
        ):
            return
        initial_load = self.meta_skeleton.isVisible()
        if error is not None or result is None:
            self._append_log(
                f"\nCould not refresh {app_id}'s description/genres: {error}\n"
            )
            if initial_load:
                self.meta_skeleton.hide()
                self._set_description("")
            return
        metadata, hero = result
        if metadata is not None:
            details = [
                value
                for value in (metadata.developer, ", ".join(metadata.genres[:2]))
                if value
            ]
            self.meta_skeleton.hide()
            if details:
                self.meta_detail.setText("• " + " • ".join(details))
                self.meta_detail.show()
            self._set_description(metadata.description)
        elif initial_load:
            self.meta_skeleton.hide()
            self._set_description("")
        if hero:
            self.hero.set_artwork(hero)

    def _set_description(self, text: str) -> None:
        self.description_skeleton.hide()
        visible = bool(text)
        self.description_label.setText(_short_description(text) if visible else "")
        self.description_label.setVisible(visible)
        self.description_heading.setVisible(visible)

    def _show_description_loading(self) -> None:
        self.description_label.hide()
        self.description_heading.hide()
        self.description_skeleton.show()

    def game(self):
        return next((g for g in self.installed if g.slug == self.slug), None)

    def launch_game(self):
        if g := self.game():
            self.run_task(
                TASK("launching").format(name=g.name),
                lambda: launch(self.paths, g, []),
                TASK("closed").format(name=g.name),
                refresh=True,
            )

    def launch_options(self):
        game = self.game()
        if game is None:
            return
        dialog = LaunchOptionsDialog(game, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted or dialog.updated_game is None:
            return
        try:
            dialog.updated_game.save(self.paths)
        except OSError as error:
            QtWidgets.QMessageBox.warning(
                self, LAUNCH_OPTIONS("save_error_title"), str(error)
            )
            return
        self.refresh(game.slug)

    def add_selected_to_steam(self):
        if g := self.game():
            self.run_task(
                TASK("adding_to_steam").format(name=g.name),
                lambda: sync_with_restart(self.paths),
                TASK("added_to_steam").format(name=g.name),
            )

    def uninstall_selected(self):
        g = self.game()
        if g is None:
            return
        deletes_files = g.source == "meta"
        question = (
            CONFIRM("uninstall_deletes_files")
            if deletes_files
            else CONFIRM("uninstall_keeps_files")
        ).format(name=g.name)
        if not _themed_question(self, APP("name"), question):
            return

        def operation():
            remove(self.paths, g)
            if g.source != "steam":
                sync_with_restart(self.paths)
            return None

        self.run_task(
            TASK("removing").format(name=g.name),
            operation,
            TASK("removed").format(name=g.name),
            refresh=True,
        )

    def refresh_library(self):
        installed = games(self.paths)
        if not installed:
            self.refresh()
            return
        preferred = self.slug

        def operation():
            for game in installed:
                try:
                    populate_game_metadata(self.paths, game, refresh=True)
                except RiftLiftError as error:
                    print(f"warning: could not refresh {game.name}: {error}")
            return preferred

        self.run_task(
            TASK("refreshing_library"),
            operation,
            TASK("library_refreshed"),
            refresh=True,
        )

    def open_folder(self):
        if g := self.game():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(g.directory))

    def open_store(self):
        if (g := self.game()) and g.source != "local":
            fallback = (
                f"https://store.steampowered.com/app/{g.steam_app_id or g.app_id}/"
                if g.source == "steam"
                else f"https://www.meta.com/experiences/pcvr/{g.app_id}/"
            )
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(g.store_url or fallback))
        elif g is None and self.selected_owned is not None:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(self.selected_owned.store_url))

    def refresh_owned(self):
        def worker():
            try:
                with contextlib.redirect_stdout(Output(self.events.output.emit)):
                    token = runtime_access_token(self.paths)
                    owned = list_owned_pcvr_apps(token)
                    icons = {
                        app.app_id: fetch_owned_icon(self.paths, app.app_id)
                        for app in owned
                    }
                self.owned_events.complete.emit((owned, icons), None)
            except Exception as error:
                self.owned_events.complete.emit(([], {}), error)

        threading.Thread(
            target=worker, daemon=True, name="riftlift-owned-refresh"
        ).start()

    def _finish_owned_scan(self, result, error):
        self._owned_loaded = True
        if error is not None:
            self.status.setText(str(error))
            if self.selected_owned or self._pending_owned_app_id:
                # We were waiting on this fetch to restore an owned-game
                # selection; now that it's failed, stop waiting and fall back.
                self._reselect()
            return
        owned, icons = result
        self.owned = owned
        self._owned_icons = icons
        self._render_tree()

    def install_owned(self):
        if self.selected_owned is not None:
            self.add_dialog(
                initial_url=self.selected_owned.store_url,
                simple_name=self.selected_owned.name,
            )

    def add_dialog(self, initial_url: str = "", simple_name: str = ""):
        dialog = StoreGameDialog(
            self.paths,
            self.local_dialog,
            self,
            initial_url=initial_url,
            simple_name=simple_name,
        )
        if dialog.exec() != QtWidgets.QDialog.Accepted or dialog.installed_game is None:
            return
        self.status.setText(
            TASK("installed_name").format(name=dialog.installed_game.name)
        )
        self.refresh(dialog.installed_game.slug)
        self.refresh_owned()

    def local_dialog(self):
        dialog = LocalGameDialog(self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return

        def operation():
            game = add_local(
                self.paths,
                dialog.executable,
                name=dialog.game_name,
                arguments=dialog.arguments,
                artwork=dialog.artwork,
            )
            if dialog.sync_steam:
                sync_with_restart(self.paths)
            return game.slug

        self.run_task(TASK("adding_local_game"), operation, refresh=True)

    def run_task(self, label, operation, success="Done", refresh=False):
        if self.busy:
            self.status.setText(STATUS("busy"))
            return
        self.busy = True
        self.busy_label = label
        self.status.setText(label + "…")
        self.addbtn.setEnabled(False)
        self.refresh_button.setEnabled(False)

        def worker():
            try:
                with (
                    contextlib.redirect_stdout(Output(self.events.output.emit)),
                    contextlib.redirect_stderr(Output(self.events.output.emit)),
                ):
                    result = operation()
                self.events.complete.emit(success, result, refresh, None)
            except Exception as error:
                self.events.complete.emit("", None, False, error)

        threading.Thread(target=worker, daemon=True, name="riftlift-operation").start()

    def _finish(self, message, result, refresh, error):
        self.busy = False
        self.busy_label = ""
        self.addbtn.setEnabled(True)
        self.refresh_button.setEnabled(True)
        # Any task can be the one that just made (or failed to make) the
        # compatibility runtime ready - not just setup itself - so the
        # banner is re-checked either way, not only on a successful run.
        self._check_setup_status()
        if error:
            self.status.setText(str(error))
            self._append_log(f"\nError: {error}\n")
            _themed_error(self, APP("name"), str(error))
        else:
            self.status.setText(message)
            if self.view_stack.currentIndex() == 1:
                self._check_system_status()
            if refresh:
                self.refresh(
                    result
                    if isinstance(result, str)
                    else refresh
                    if isinstance(refresh, str)
                    else self.slug
                )
                self._render_tree()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
            self.status.setText(
                f"{self.busy_label} is still running; minimize RiftLift instead"
            )
            return
        super().closeEvent(event)

    def _append_log(self, value):
        self.log = (self.log + value)[-30000:]
        for view in list(self.log_views):
            if not view.isVisible():
                self.log_views.remove(view)
            else:
                view.setPlainText(self.log)
                view.moveCursor(QtGui.QTextCursor.End)

    def show_activity(self):
        d = QtWidgets.QDialog(self)
        d.setWindowTitle(ACTIVITY("title"))
        d.resize(800, 440)
        d.setStyleSheet(STYLE)
        layout = wrap_dialog(d, ACTIVITY("title"))
        layout.addWidget(self.label(ACTIVITY("heading"), "game"))
        view = QtWidgets.QTextEdit(readOnly=True)
        view.setPlainText(self.log or "No activity yet.\n")
        layout.addWidget(view)
        self.log_views.append(view)
        d.finished.connect(
            lambda: self.log_views.remove(view) if view in self.log_views else None
        )
        d.exec()


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("RiftLift")
    app.setStyle("Fusion")
    window = Window()
    app._riftlift_window = window
    window.show()
    return app.exec()
