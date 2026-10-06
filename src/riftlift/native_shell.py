"""Native library presentation, independent of authentication and runtime backends.

The host supplies the existing Window callbacks. This boundary also permits safe,
offline visual previews on platforms whose backend is maintained separately.
"""

from __future__ import annotations

from importlib.resources import files

from PySide6 import QtCore, QtGui, QtWidgets

from .i18n import namespace
from .native_theme import STYLE

NAV = namespace("nav")
GAME = namespace("game")
LIBRARY = namespace("library")
SHELL = namespace("shell")


def brand_icon() -> QtGui.QIcon:
    return QtGui.QIcon(str(files("riftlift").joinpath("assets/mark.svg")))


class Artwork(QtWidgets.QWidget):
    """Artwork surface with a quiet original portal fallback; no network fetches."""

    def __init__(self):
        super().__init__()
        self.hero = QtGui.QPixmap()
        self.setMinimumHeight(210)
        self.setMaximumHeight(330)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )

    def set_artwork(self, path: str) -> None:
        self.hero = QtGui.QPixmap(path)
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        clip = QtGui.QPainterPath()
        clip.addRoundedRect(QtCore.QRectF(self.rect()), 18, 18)
        painter.setClipPath(clip)
        background = QtGui.QLinearGradient(0, 0, self.width(), self.height())
        background.setColorAt(0, QtGui.QColor("#142526"))
        background.setColorAt(1, QtGui.QColor("#212b45"))
        painter.fillRect(self.rect(), background)
        if not self.hero.isNull():
            pixmap = self.hero.scaled(
                self.size(),
                QtCore.Qt.KeepAspectRatioByExpanding,
                QtCore.Qt.SmoothTransformation,
            )
            painter.drawPixmap(
                (self.width() - pixmap.width()) // 2,
                (self.height() - pixmap.height()) // 2,
                pixmap,
            )
        else:
            # Receding arches suggest passage between two VR environments.
            painter.translate(self.width() * 0.72, self.height() * 0.58)
            for index in range(6, 0, -1):
                scale = 30 + index * 22
                painter.setPen(
                    QtGui.QPen(QtGui.QColor(117, 223, 203, 30 + index * 9), 2)
                )
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawRoundedRect(
                    QtCore.QRectF(-scale * 0.65, -scale, scale * 1.3, scale * 2),
                    scale * 0.65,
                    scale * 0.65,
                )
        painter.resetTransform()
        shade = QtGui.QLinearGradient(0, 0, self.width(), 0)
        shade.setColorAt(0, QtGui.QColor(12, 18, 24, 90))
        shade.setColorAt(1, QtGui.QColor(12, 18, 24, 0))
        painter.fillRect(self.rect(), shade)


class Placeholder(QtWidgets.QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("placeholder")
        self.setFixedHeight(12)
        self.hide()


class AccessibleButton(QtWidgets.QPushButton):
    """Keep screen-reader names current when backend state changes the label."""

    def setText(self, text):
        super().setText(text)
        self.setAccessibleName(text)


class NativePresentation:
    """Widget composition used by a backend host and the offline preview host."""

    def label(self, text="", name=""):
        widget = QtWidgets.QLabel(text)
        widget.setObjectName(name)
        widget.setTextFormat(QtCore.Qt.PlainText)
        return widget

    def button(self, text, callback, primary=False):
        widget = AccessibleButton(text)
        widget.setObjectName("primary" if primary else "")
        widget.setCursor(QtCore.Qt.PointingHandCursor)
        widget.setAccessibleName(text)
        widget.clicked.connect(callback)
        return widget

    def _build(self):
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.FramelessWindowHint)
        self.setWindowIcon(brand_icon())
        self.setMinimumSize(800, 600)
        self.resize(1200, 790)
        self.setStyleSheet(STYLE)
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(24, 20, 24, 14)
        outer.setSpacing(18)

        header = QtWidgets.QHBoxLayout()
        mark = QtWidgets.QLabel()
        mark.setPixmap(brand_icon().pixmap(38, 38))
        header.addWidget(mark)
        header.addSpacing(6)
        header.addWidget(self.label("RiftLift", "brand"))
        header.addSpacing(16)
        self.now_playing_icon = QtWidgets.QLabel()
        self.now_playing_icon.setFixedSize(24, 24)
        self.now_playing_icon.hide()
        header.addWidget(self.now_playing_icon)
        self.now_playing_label = self.label("", "muted")
        self.now_playing_label.setTextFormat(QtCore.Qt.RichText)
        self.now_playing_label.hide()
        header.addWidget(self.now_playing_label)
        header.addStretch()
        self.settings_button = self.button(NAV("settings"), self._toggle_settings)
        self.signin = self.button(NAV("sign_in"), self.show_auth)
        header.addWidget(self.settings_button)
        header.addWidget(self.signin)
        outer.addLayout(header)

        self.setup_banner = QtWidgets.QWidget()
        self.setup_banner.setObjectName("setup_banner")
        banner_layout = QtWidgets.QHBoxLayout(self.setup_banner)
        banner_label = self.label(namespace("setup")("banner_text"), "muted")
        banner_label.setWordWrap(True)
        banner_layout.addWidget(banner_label, 1)
        banner_layout.addWidget(
            self.button(namespace("setup")("run_now"), self._run_setup)
        )
        self.setup_banner.hide()
        outer.addWidget(self.setup_banner)

        self.view_stack = QtWidgets.QStackedWidget()
        main_view = QtWidgets.QWidget()
        content = QtWidgets.QHBoxLayout(main_view)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(24)
        sidebar = QtWidgets.QWidget()
        sidebar.setMinimumWidth(210)
        sidebar.setMaximumWidth(275)
        left = QtWidgets.QVBoxLayout(sidebar)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(self.label(LIBRARY("title"), "section"))
        heading.addStretch()
        self.refresh_button = self.button("↻", self.refresh_all)
        self.refresh_button.setObjectName("refresh")
        self.refresh_button.setFixedSize(36, 36)
        self.refresh_button.setAccessibleName(LIBRARY("refresh_tooltip"))
        self.refresh_button.setToolTip(LIBRARY("refresh_tooltip"))
        heading.addWidget(self.refresh_button)
        left.addLayout(heading)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText(SHELL("search"))
        self.search.setAccessibleName(SHELL("search"))
        self.search.setClearButtonEnabled(True)
        left.addWidget(self.search)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setAccessibleName(LIBRARY("title"))
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QtCore.QSize(36, 36))
        self.tree.setIndentation(0)
        self.tree.setRootIsDecorated(False)
        self.tree.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.tree.currentItemChanged.connect(self._tree_item_changed)
        self.tree.itemClicked.connect(self._category_clicked)
        self.tree.itemExpanded.connect(self._category_toggled)
        self.tree.itemCollapsed.connect(self._category_toggled)
        self._categories = []
        self._installed_category = self._add_category("installed")
        self._steam_category = self._add_category("installed_steam")
        self._owned_category = self._add_category("not_installed")
        self.tree.expandAll()
        self.search.textChanged.connect(self._filter_library)
        left.addWidget(self.tree, 1)
        self.search_empty = self.label(SHELL("no_matches"), "muted")
        self.search_empty.setWordWrap(True)
        self.search_empty.hide()
        left.addWidget(self.search_empty)
        self.steam_games = self.button(NAV("steam_games"), self.steam_dialog)
        self.addbtn = self.button(NAV("add_game"), lambda: self.add_dialog())
        left.addWidget(self.steam_games)
        left.addWidget(self.addbtn)
        content.addWidget(sidebar, 1)

        self.stack = QtWidgets.QStackedWidget()
        empty = QtWidgets.QWidget()
        empty_layout = QtWidgets.QVBoxLayout(empty)
        empty_layout.addStretch()
        empty_layout.addWidget(self.label(SHELL("welcome"), "game"))
        hint = self.label(SHELL("welcome_hint"), "description")
        hint.setWordWrap(True)
        empty_layout.addWidget(hint)
        empty_layout.addSpacing(16)
        empty_layout.addWidget(
            self.button(NAV("sign_in"), self.show_auth, True),
            alignment=QtCore.Qt.AlignLeft,
        )
        empty_layout.addStretch()
        self.stack.addWidget(empty)
        self.detail = self._native_detail()
        self.stack.addWidget(self.detail)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.stack)
        content.addWidget(scroll, 3)
        self.view_stack.addWidget(main_view)
        settings = QtWidgets.QScrollArea()
        settings.setWidgetResizable(True)
        settings.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.settings_page = self._make_settings()
        settings.setWidget(self.settings_page)
        self.view_stack.addWidget(settings)
        outer.addWidget(self.view_stack, 1)

        footer = QtWidgets.QHBoxLayout()
        self.status = self.label(namespace("status")("ready"), "muted")
        self.status.setWordWrap(True)
        self.status.setAccessibleName(SHELL("activity_status"))
        footer.addWidget(self.status, 1)
        footer.addWidget(
            self.button(namespace("status")("view_activity"), self.show_activity)
        )
        outer.addLayout(footer)
        self._shortcuts = []
        for key, callback in (
            ("Ctrl+F", self.search.setFocus),
            ("Ctrl+R", self.refresh_all),
            ("Escape", self._leave_settings),
        ):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
        QtWidgets.QWidget.setTabOrder(self.search, self.tree)

    def _native_detail(self):
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.hero = Artwork()
        layout.addWidget(self.hero, 1)
        self.game_name = self.label("", "game")
        self.game_name.setWordWrap(True)
        title_row = QtWidgets.QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(self.game_name)
        layout.addLayout(title_row)
        self.meta_row = QtWidgets.QHBoxLayout()
        self.meta = self.label("", "muted")
        self.meta.setWordWrap(True)
        self.meta_detail = self.label("", "muted")
        self.meta_detail.setWordWrap(True)
        self.meta_skeleton = Placeholder()
        for widget in (self.meta, self.meta_detail, self.meta_skeleton):
            self.meta_row.addWidget(widget)
        layout.addLayout(self.meta_row)
        actions = QtWidgets.QHBoxLayout()
        self.launch = self.button(GAME("launch"), self.launch_game, True)
        self.install_here = self.button(GAME("install"), self.install_owned, True)
        actions.addWidget(self.launch)
        actions.addWidget(self.install_here)
        self.files_button = QtWidgets.QToolButton()
        self.files_button.setIcon(
            QtGui.QIcon(str(files("riftlift").joinpath("assets/folder.svg")))
        )
        self.files_button.setIconSize(QtCore.QSize(22, 22))
        self.files_button.setAccessibleName(GAME("files"))
        self.files_button.setToolTip(GAME("files"))
        self.files_button.setObjectName("quiet")
        self.files_button.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.files_button.clicked.connect(self.open_folder)
        title_row.addWidget(self.files_button, 0, QtCore.Qt.AlignVCenter)
        title_row.addStretch()
        actions.addStretch()
        self.more_button = QtWidgets.QToolButton()
        self.more_button.setText("•••")
        self.more_button.setAccessibleName(SHELL("game_actions"))
        self.more_button.setToolTip(SHELL("game_actions"))
        self.more_button.setObjectName("quiet")
        self.more_button.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.more_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.game_menu = QtWidgets.QMenu(self.more_button)

        def action(text, callback):
            item = self.game_menu.addAction(text)
            item.triggered.connect(callback)
            return item

        self.options_button = action(GAME("launch_options"), self.launch_options)
        self.game_menu.addSeparator()
        self.uninstall_button = action(GAME("uninstall"), self.uninstall_selected)
        self.steam_status_action = self.game_menu.addAction(
            SHELL("steam_status_unknown")
        )
        self.steam_status_action.setEnabled(False)
        self.steam_status_action.setVisible(False)
        self.more_button.setMenu(self.game_menu)
        actions.addWidget(self.more_button)
        layout.addLayout(actions)
        self.description_heading = self.label(GAME("about"), "section")
        self.description_heading.setParent(page)
        self.description_heading.hide()
        self.description_label = self.label("", "description")
        self.description_label.setWordWrap(True)
        self.description_label.setTextInteractionFlags(
            QtCore.Qt.TextSelectableByMouse | QtCore.Qt.TextSelectableByKeyboard
        )
        layout.addWidget(self.description_label)
        self.description_skeleton = Placeholder()
        layout.addWidget(self.description_skeleton)
        self.description_skeleton.hide()
        links = QtWidgets.QHBoxLayout()
        self.store_link = self.button(GAME("open_rift_store"), self.open_store)
        self.store_link.setObjectName("link")
        links.addWidget(self.store_link)
        self.add_steam_button = self.button(
            GAME("add_to_steam"), self.add_selected_to_steam
        )
        self.add_steam_button.setObjectName("link")
        links.addWidget(self.add_steam_button)
        links.addStretch()
        layout.addLayout(links)
        layout.addStretch()
        return page

    def _filter_library(self, text=""):
        query = text.strip().casefold()
        matches = 0
        for category, _key in self._categories:
            visible = 0
            for index in range(category.childCount()):
                item = category.child(index)
                match = query in item.text(0).casefold()
                item.setHidden(not match)
                visible += int(match)
            category.setHidden(visible == 0)
            if query and visible:
                category.setExpanded(True)
            matches += visible
        self.search_empty.setVisible(bool(query) and matches == 0)
