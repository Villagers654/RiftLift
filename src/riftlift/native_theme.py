"""Reusable native library design tokens and accessible Qt widget states."""

from importlib.resources import files

# Brand palette: deep navy surfaces around the RiftLift blue.
BACKGROUND = "#0c111a"
# A faint top wash only; a stronger glow made panels look like they float.
GLOW = "#0f1828"
PANEL = "#111827"
CONTROL = "#172033"
CONTROL_HOVER = "#1e2a42"
CONTROL_PRESSED = "#24324f"
BORDER = "#222e45"
BORDER_STRONG = "#33425f"
TEXT = "#eef2f8"
TEXT_SOFT = "#b8c2d3"
TEXT_MUTED = "#8b98ae"
TEXT_DISABLED = "#5d6a80"
BLUE = "#0a7cff"
BLUE_LIGHT = "#3b9bff"
BLUE_DEEP = "#0057e7"
FOCUS = "#5aa9ff"
SELECTION = "rgba(10, 124, 255, 0.16)"
SELECTION_BORDER = "rgba(59, 155, 255, 0.55)"
SUCCESS = "#34d399"
DANGER = "#ff7a85"

ACCENT_GRADIENT = (
    f"qlineargradient(x1:0, y1:1, x2:1, y2:0, stop:0 {BLUE_DEEP}, stop:1 {BLUE_LIGHT})"
)
ACCENT_GRADIENT_HOVER = (
    "qlineargradient(x1:0, y1:1, x2:1, y2:0, stop:0 #1a6dff, stop:1 #62b2ff)"
)
_CHECK = files("riftlift").joinpath("assets/check.svg").as_posix()

STYLE = f"""
QWidget {{ background: transparent; color: {TEXT}; font: 14px 'Segoe UI'; }}
QMainWindow {{ background: {BACKGROUND}; }}
QDialog {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {GLOW}, stop:0.3 {BACKGROUND}, stop:1 {BACKGROUND}); }}
QWidget#root {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {GLOW}, stop:0.3 {BACKGROUND}, stop:1 {BACKGROUND}); }}
QWidget#sidebar {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 16px; }}
QToolTip {{ background: {CONTROL}; color: {TEXT}; border: 1px solid {BORDER_STRONG};
 border-radius: 6px; padding: 6px 8px; }}
QLabel#brand {{ font-size: 23px; font-weight: 700; }}
QLabel#game {{ font-size: 32px; font-weight: 700; }}
QLabel#section {{ font-size: 17px; font-weight: 600; }}
QLabel#muted {{ color: {TEXT_MUTED}; font-size: 12px; }}
QLabel#description {{ color: {TEXT_SOFT}; font-size: 14px; }}

QPushButton {{ background: {CONTROL}; border: 1px solid {BORDER}; border-radius: 10px;
 padding: 9px 16px; min-height: 20px; }}
QPushButton:hover {{ background: {CONTROL_HOVER}; border-color: {BORDER_STRONG}; }}
QPushButton:pressed {{ background: {CONTROL_PRESSED}; }}
QPushButton:focus, QLineEdit:focus, QComboBox:focus {{ border: 2px solid {FOCUS}; }}
QPushButton:disabled {{ color: {TEXT_DISABLED}; background: {PANEL}; border-color: {BORDER}; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {TEXT_SOFT}; }}
QPushButton#ghost:hover {{ background: {CONTROL}; border-color: {BORDER}; color: {TEXT}; }}
QPushButton#ghost:focus {{ border: 2px solid {FOCUS}; }}
QPushButton#primary {{ background: {ACCENT_GRADIENT}; color: #ffffff; border: 1px solid {BLUE};
 font-weight: 700; min-width: 100px; }}
QPushButton#primary:hover {{ background: {ACCENT_GRADIENT_HOVER}; border-color: {BLUE_LIGHT}; }}
QPushButton#primary:pressed {{ background: {BLUE_DEEP}; }}
QPushButton#primary:focus {{ border: 2px solid #ffffff; }}
QPushButton#primary:disabled {{ color: {TEXT_DISABLED}; background: {CONTROL}; border-color: {BORDER}; }}
QPushButton[size="large"] {{ padding: 12px 34px; font-size: 15px; border-radius: 12px; }}
QPushButton#danger {{ color: {DANGER}; background: transparent; border-color: #5a2f3c; }}
QPushButton#link {{ background: transparent; border: 1px solid transparent; color: {TEXT_MUTED};
 padding: 3px 0; min-height: 18px; }}
QPushButton#link:hover {{ color: {BLUE_LIGHT}; }}
QPushButton#link:focus {{ border: 2px solid {FOCUS}; }}
QPushButton#refresh {{ padding: 0; min-height: 0; font-size: 20px; color: {TEXT_SOFT};
 background: transparent; border-color: transparent; }}
QPushButton#refresh:hover {{ background: {CONTROL}; border-color: {BORDER}; color: {TEXT}; }}

QToolButton#quiet {{ background: transparent; border: 1px solid transparent; border-radius: 8px;
 color: {TEXT_MUTED}; font-size: 20px; padding: 3px 10px; }}
QToolButton#quiet:hover {{ background: {CONTROL}; color: {TEXT}; }}
QToolButton#quiet:focus {{ border: 2px solid {FOCUS}; }}
QToolButton::menu-indicator {{ image: none; }}
QMenu {{ background: {CONTROL}; border: 1px solid {BORDER_STRONG}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 9px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SELECTION}; color: #ffffff; }}
QMenu::item:disabled {{ color: {TEXT_DISABLED}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}

QLineEdit, QComboBox, QTextEdit, QPlainTextEdit, QListWidget {{ background: {CONTROL};
 border: 1px solid {BORDER}; border-radius: 10px; padding: 9px 11px;
 selection-background-color: {BLUE}; }}
QLineEdit:hover, QComboBox:hover {{ border-color: {BORDER_STRONG}; }}
QComboBox::drop-down {{ border: 0; width: 26px; }}
QComboBox QAbstractItemView {{ background: {CONTROL}; color: {TEXT}; border: 1px solid {BORDER_STRONG};
 selection-background-color: {SELECTION}; outline: 0; }}
QListWidget {{ padding: 6px; outline: 0; }}
QListWidget::item {{ padding: 9px 10px; border-radius: 8px; border: 1px solid transparent; }}
QListWidget::item:hover {{ background: {CONTROL_HOVER}; }}
QListWidget::item:selected {{ background: {SELECTION}; border-color: {SELECTION_BORDER}; color: #ffffff; }}

QCheckBox {{ spacing: 10px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px;
 border: 1px solid {BORDER_STRONG}; background: {CONTROL}; }}
QCheckBox::indicator:hover {{ border-color: {FOCUS}; }}
QCheckBox::indicator:checked {{ background: {BLUE}; border-color: {BLUE}; image: url({_CHECK}); }}
QCheckBox::indicator:disabled {{ background: {PANEL}; border-color: {BORDER}; }}

QTreeWidget {{ background: transparent; border: 2px solid transparent; outline: 0; }}
QTreeWidget::item {{ border: 1px solid transparent; border-radius: 10px; padding: 10px 8px; margin: 2px 0; }}
QTreeWidget::item:hover {{ background: {CONTROL}; }}
QTreeWidget::item:selected {{ background: {SELECTION}; border-color: {SELECTION_BORDER}; color: #ffffff; }}
QTreeWidget::item:has-children {{ color: {TEXT_MUTED}; font-size: 11px; font-weight: 600; padding-top: 12px; }}

QScrollArea, QStackedWidget {{ border: 0; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: #46577a; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QWidget#setup_banner {{ background: rgba(10, 124, 255, 0.10); border: 1px solid {SELECTION_BORDER};
 border-radius: 12px; }}
QFrame#placeholder {{ background: {CONTROL}; border-radius: 5px; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 16px; }}
QProgressBar {{ border: 0; border-radius: 4px; background: {CONTROL}; text-align: center;
 max-height: 8px; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT_GRADIENT}; border-radius: 4px; }}
"""
