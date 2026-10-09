"""Native library presentation, independent of authentication and runtime backends.

The host supplies the existing Window callbacks. This boundary also permits safe,
offline visual previews on platforms whose backend is maintained separately.
"""

from __future__ import annotations

from importlib.resources import files

from PySide6 import QtCore, QtGui, QtWidgets

from .i18n import namespace
from .native_theme import BLUE_LIGHT, CONTROL_HOVER, STYLE, TEXT_SOFT

NAV = namespace("nav")
GAME = namespace("game")
LIBRARY = namespace("library")
SHELL = namespace("shell")


def brand_icon() -> QtGui.QIcon:
    return QtGui.QIcon(str(files("riftlift").joinpath("assets/mark.svg")))


def rounded_icon(path: str, size: int = 40, radius: float = 9) -> QtGui.QIcon:
    """Library artwork as a rounded tile, rendered for high-DPI screens."""
    source = QtGui.QPixmap(path)
    if source.isNull():
        return QtGui.QIcon()
    scale = 3
    tile = QtGui.QPixmap(size * scale, size * scale)
    tile.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(tile)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    clip = QtGui.QPainterPath()
    clip.addRoundedRect(QtCore.QRectF(tile.rect()), radius * scale, radius * scale)
    painter.setClipPath(clip)
    scaled = source.scaled(
        tile.size(),
        QtCore.Qt.KeepAspectRatioByExpanding,
        QtCore.Qt.SmoothTransformation,
    )
    painter.drawPixmap(
        (tile.width() - scaled.width()) // 2,
        (tile.height() - scaled.height()) // 2,
        scaled,
    )
    painter.end()
    tile.setDevicePixelRatio(scale)
    return QtGui.QIcon(tile)


def placeholder_icon(name: str, size: int = 34, radius: float = 8) -> QtGui.QIcon:
    """A tile with the game's initial, keeping rows aligned when art is missing."""
    scale = 3
    tile = QtGui.QPixmap(size * scale, size * scale)
    tile.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(tile)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor(CONTROL_HOVER))
    painter.drawRoundedRect(QtCore.QRectF(tile.rect()), radius * scale, radius * scale)
    font = QtGui.QFont("Segoe UI")
    font.setPixelSize(int(size * scale * 0.45))
    font.setWeight(QtGui.QFont.DemiBold)
    painter.setFont(font)
    painter.setPen(QtGui.QColor(TEXT_SOFT))
    initial = next((c for c in name if c.isalnum()), "?").upper()
    painter.drawText(tile.rect(), QtCore.Qt.AlignCenter, initial)
    painter.end()
    tile.setDevicePixelRatio(scale)
    return QtGui.QIcon(tile)


def key_art(pixmap: QtGui.QPixmap) -> QtGui.QPixmap:
    """Recover the clean 16:9 key art from a Steam-style composite banner.

    Composite banners (metadata._composite) centre the original at 94% height over
    a blurred copy of itself; showing them whole looks smeared in the app.
    """
    if pixmap.isNull() or (pixmap.width(), pixmap.height()) != (1920, 620):
        return pixmap
    height = int(620 * 0.94)
    width = round(height * 16 / 9)
    return pixmap.copy((1920 - width) // 2, (620 - height) // 2, width, height)


def _top_rounded(rect: QtCore.QRectF, radius: float) -> QtGui.QPainterPath:
    path = QtGui.QPainterPath()
    path.moveTo(rect.left(), rect.bottom())
    path.lineTo(rect.left(), rect.top() + radius)
    path.arcTo(rect.left(), rect.top(), radius * 2, radius * 2, 180, -90)
    path.lineTo(rect.right() - radius, rect.top())
    path.arcTo(rect.right() - radius * 2, rect.top(), radius * 2, radius * 2, 90, -90)
    path.lineTo(rect.right(), rect.bottom())
    path.closeSubpath()
    return path


class Artwork(QtWidgets.QWidget):
    """Artwork surface with a quiet original portal fallback; no network fetches."""

    def __init__(self):
        super().__init__()
        self.hero = QtGui.QPixmap()
        policy = QtWidgets.QSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred
        )
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        # Banner proportions that keep the key art recognisable at any width.
        return int(min(max(width * 0.4, 220), 380))

    def sizeHint(self):
        return QtCore.QSize(800, self.heightForWidth(800))

    def set_artwork(self, path: str) -> None:
        self.hero = key_art(QtGui.QPixmap(path))
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        clip = _top_rounded(QtCore.QRectF(self.rect()), 15)
        painter.setClipPath(clip)
        background = QtGui.QLinearGradient(0, 0, self.width(), self.height())
        background.setColorAt(0, QtGui.QColor("#0d1a36"))
        background.setColorAt(1, QtGui.QColor("#13306a"))
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
                    QtGui.QPen(QtGui.QColor(90, 169, 255, 30 + index * 9), 2)
                )
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawRoundedRect(
                    QtCore.QRectF(-scale * 0.65, -scale, scale * 1.3, scale * 2),
                    scale * 0.65,
                    scale * 0.65,
                )
        painter.resetTransform()
        # Settle the artwork into the page and give the card a crisp edge.
        shade = QtGui.QLinearGradient(0, self.height() * 0.55, 0, self.height())
        shade.setColorAt(0, QtGui.QColor(12, 17, 26, 0))
        shade.setColorAt(1, QtGui.QColor(12, 17, 26, 150))
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
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint, False)
        self.setWindowIcon(brand_icon())
        self.setMinimumSize(800, 600)
        self.resize(1200, 790)
        self.setStyleSheet(STYLE)
        root = QtWidgets.QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(16)

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

        # Two columns: the sidebar carries the brand, library and navigation;
        # the right column shows the selected game or Settings.
        content = QtWidgets.QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(16)
        sidebar = QtWidgets.QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        sidebar.setMinimumWidth(250)
        sidebar.setMaximumWidth(320)
        left = QtWidgets.QVBoxLayout(sidebar)
        left.setContentsMargins(14, 16, 14, 12)
        left.setSpacing(10)

        brand_row = QtWidgets.QHBoxLayout()
        brand_row.setContentsMargins(4, 0, 0, 0)
        brand_row.setSpacing(10)
        mark = QtWidgets.QLabel()
        mark.setPixmap(brand_icon().pixmap(30, 30))
        brand_row.addWidget(mark)
        brand = QtWidgets.QLabel(f"Rift<span style='color:{BLUE_LIGHT}'>Lift</span>")
        brand.setObjectName("brand")
        brand.setTextFormat(QtCore.Qt.RichText)
        brand.setAccessibleName("RiftLift")
        brand_row.addWidget(brand)
        brand_row.addStretch()
        left.addLayout(brand_row)
        left.addSpacing(6)

        heading = QtWidgets.QHBoxLayout()
        heading.setSpacing(8)
        self.refresh_button = self.button("↻", self.refresh_all)
        self.refresh_button.setObjectName("refresh")
        self.refresh_button.setFixedSize(40, 40)
        self.refresh_button.setAccessibleName(LIBRARY("refresh_tooltip"))
        self.refresh_button.setToolTip(LIBRARY("refresh_tooltip"))
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText(SHELL("search"))
        self.search.setAccessibleName(SHELL("search"))
        self.search.setClearButtonEnabled(True)
        heading.addWidget(self.search, 1)
        heading.addWidget(self.refresh_button)
        left.addLayout(heading)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setAccessibleName(LIBRARY("title"))
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QtCore.QSize(34, 34))
        self.tree.setIndentation(0)
        self.tree.setRootIsDecorated(False)
        self.tree.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.tree.currentItemChanged.connect(self._tree_item_changed)
        self.tree.itemClicked.connect(self._category_clicked)
        self.tree.itemClicked.connect(self._return_to_library)
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

        now_playing = QtWidgets.QHBoxLayout()
        now_playing.setContentsMargins(4, 4, 0, 0)
        now_playing.setSpacing(10)
        self.now_playing_icon = QtWidgets.QLabel()
        self.now_playing_icon.setFixedSize(24, 24)
        self.now_playing_icon.hide()
        now_playing.addWidget(self.now_playing_icon)
        self.now_playing_label = self.label("", "muted")
        self.now_playing_label.setTextFormat(QtCore.Qt.RichText)
        self.now_playing_label.hide()
        now_playing.addWidget(self.now_playing_label, 1)
        left.addLayout(now_playing)

        divider = QtWidgets.QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        left.addSpacing(2)
        left.addWidget(divider)
        assets = files("riftlift").joinpath("assets")
        self.settings_button = self.button(NAV("settings"), self._toggle_settings)
        self.settings_button.setCheckable(True)
        self.signin = self.button(NAV("sign_in"), self.show_auth)
        for widget, icon in (
            (self.settings_button, "settings"),
            (self.signin, "account"),
        ):
            widget.setObjectName("nav")
            widget.setIcon(QtGui.QIcon(str(assets.joinpath(f"{icon}.svg"))))
            widget.setIconSize(QtCore.QSize(20, 20))
            left.addWidget(widget)
        content.addWidget(sidebar, 1)

        self.stack = QtWidgets.QStackedWidget()
        self.stack.setObjectName("content")
        self.stack.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        empty = QtWidgets.QWidget()
        empty_layout = QtWidgets.QVBoxLayout(empty)
        empty_layout.setContentsMargins(36, 32, 36, 32)
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
        self.view_stack = QtWidgets.QStackedWidget()
        self.view_stack.addWidget(scroll)
        settings = QtWidgets.QScrollArea()
        settings.setWidgetResizable(True)
        settings.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.settings_page = self._make_settings()
        settings.setWidget(self.settings_page)
        self.view_stack.addWidget(settings)
        content.addWidget(self.view_stack, 3)
        outer.addLayout(content, 1)

        # Task handlers retain their status text internally without a footer.
        self.status = self.label(namespace("status")("ready"), "muted")
        self.status.setParent(self)
        self.status.hide()
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
        # The page is a panel like the sidebar: artwork across the top edge,
        # details padded beneath it.
        layout.setContentsMargins(1, 1, 1, 0)
        layout.setSpacing(0)
        self.hero = Artwork()
        layout.addWidget(self.hero)
        body = QtWidgets.QVBoxLayout()
        body.setContentsMargins(28, 20, 28, 24)
        body.setSpacing(12)
        layout.addLayout(body, 1)
        self.game_name = self.label("", "game")
        self.game_name.setWordWrap(False)
        body.addSpacing(6)
        body.addWidget(self.game_name)
        self.meta_row = QtWidgets.QHBoxLayout()
        self.meta = self.label("", "muted")
        self.meta.setWordWrap(True)
        self.meta_detail = self.label("", "muted")
        self.meta_detail.setWordWrap(True)
        self.meta_skeleton = Placeholder()
        for widget in (self.meta, self.meta_detail, self.meta_skeleton):
            self.meta_row.addWidget(widget)
        body.addLayout(self.meta_row)
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(10)
        self.launch = self.button(GAME("launch"), self.launch_game, True)
        self.install_here = self.button(GAME("install"), self.install_owned, True)
        for primary in (self.launch, self.install_here):
            primary.setProperty("size", "large")
        actions.addWidget(self.launch)
        actions.addWidget(self.install_here)
        self.files_button = QtWidgets.QToolButton()
        self.files_button.setIcon(
            QtGui.QIcon(str(files("riftlift").joinpath("assets/folder.svg")))
        )
        self.files_button.setIconSize(QtCore.QSize(22, 22))
        self.files_button.setAccessibleName(GAME("files"))
        self.files_button.setToolTip(GAME("files"))
        self.files_button.setObjectName("square")
        self.files_button.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.files_button.clicked.connect(self.open_folder)
        actions.addWidget(self.files_button)
        self.more_button = QtWidgets.QToolButton()
        self.more_button.setText("•••")
        self.more_button.setAccessibleName(SHELL("game_actions"))
        self.more_button.setToolTip(SHELL("game_actions"))
        self.more_button.setObjectName("square")
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
        actions.addStretch()
        body.addSpacing(4)
        body.addLayout(actions)
        body.addSpacing(6)
        self.description_heading = self.label(GAME("about"), "section")
        self.description_heading.setParent(page)
        self.description_heading.hide()
        self.description_label = self.label("", "description")
        self.description_label.setWordWrap(True)
        self.description_label.setMaximumWidth(760)
        self.description_label.setTextInteractionFlags(
            QtCore.Qt.TextSelectableByMouse | QtCore.Qt.TextSelectableByKeyboard
        )
        body.addWidget(self.description_label)
        self.description_skeleton = Placeholder()
        body.addWidget(self.description_skeleton)
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
        body.addLayout(links)
        body.addStretch()
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
