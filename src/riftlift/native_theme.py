"""Reusable native library design tokens and accessible Qt widget states."""

STYLE = """
QWidget { background: #10161d; color: #eaf0f4; font: 14px 'Segoe UI'; }
QLabel { background: transparent; }
QLabel#brand { font-size: 24px; font-weight: 700; }
QLabel#game { font-size: 32px; font-weight: 700; }
QLabel#section { font-size: 17px; font-weight: 600; }
QLabel#muted { color: #a8b7c5; font-size: 12px; }
QLabel#description { color: #c4cfd8; font-size: 14px; }
QPushButton { background: #1a2530; border: 1px solid #344353; border-radius: 8px;
 padding: 9px 14px; min-height: 20px; }
QPushButton:hover { background: #263744; border-color: #749baf; }
QPushButton:pressed { background: #314854; }
QToolButton#quiet { background: transparent; border: 1px solid transparent; border-radius: 6px;
 color: #a8b7c5; font-size: 20px; padding: 3px 10px; }
QToolButton#quiet:hover { background: #263744; color: #eaf0f4; }
QToolButton#quiet:focus { border: 2px solid #9cf0db; }
QToolButton::menu-indicator { image: none; }
QMenu { background: #1a2530; border: 1px solid #344353; padding: 6px; }
QMenu::item { padding: 9px 20px; }
QMenu::item:selected { background: #233a3b; color: #effff9; }
QMenu::separator { height: 1px; background: #344353; margin: 5px 8px; }
QPushButton#link { background: transparent; border: 1px solid transparent; color: #a8b7c5; padding: 3px 0; min-height: 18px; }
QPushButton#link:hover { color: #a1e9d6; }
QPushButton#link:focus { border: 2px solid #9cf0db; }
QPushButton#refresh { padding: 0; min-height: 0; font-size: 23px; }
QPushButton:focus, QLineEdit:focus, QTreeWidget:focus { border: 2px solid #9cf0db; }
QPushButton:disabled { color: #7d8b99; background: #1a232d; border-color: #344353; }
QPushButton#primary { background: #a1e9d6; color: #102721; border-color: #a1e9d6;
 font-weight: 700; min-width: 100px; }
QPushButton#primary:hover { background: #c1f6e8; }
QPushButton#primary:focus { border: 2px solid #ffffff; }
QPushButton#primary:disabled { color: #8b9b98; background: #293d39; border-color: #455b56; }
QPushButton#danger { color: #efb5b5; background: transparent; border-color: #5c3e48; }
QLineEdit { background: #18222c; border: 1px solid #344353; border-radius: 8px; padding: 10px; }
QTreeWidget { background: transparent; border: 2px solid transparent; outline: 0; }
QTreeWidget::item { border: 1px solid transparent; border-radius: 8px; padding: 12px 8px; margin: 2px 0; }
QTreeWidget::item:hover { background: #1b2934; }
QTreeWidget::item:selected { background: #233a3b; border-color: #6cb8a8; color: #effff9; }
QTreeWidget::item:has-children { color: #93a8b8; font-size: 11px; font-weight: 600; padding-top: 14px; }
QScrollArea, QStackedWidget { border: 0; background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #405361; border-radius: 4px; min-height: 32px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QWidget#setup_banner { background: #223330; border: 1px solid #486b62; border-radius: 8px; }
QFrame#placeholder { background: #2b3c49; border-radius: 5px; }
QComboBox, QTextEdit { background: #18222c; border: 1px solid #344353; border-radius: 8px; padding: 8px; }
QComboBox QAbstractItemView { background: #18222c; color: #eaf0f4; selection-background-color: #233a3b; border: 1px solid #344353; }
QPlainTextEdit, QListWidget { background: #18222c; border: 1px solid #344353; border-radius: 8px; padding: 8px; }
QListWidget::item { padding: 8px; border-radius: 5px; }
QListWidget::item:selected { background: #233a3b; color: #effff9; }
QProgressBar { border: 1px solid #344353; border-radius: 6px; background: #18222c; text-align: center; }
QProgressBar::chunk { background: #74ccb4; border-radius: 5px; }
"""
