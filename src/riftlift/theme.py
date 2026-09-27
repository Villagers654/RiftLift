"""Shared Qt styling for RiftLift's desktop UI."""

STYLE = """
QWidget{background:#0b1020;color:#f4f7ff;font:14px sans-serif}
QMainWindow{border:1px solid #33415f} QDialog{border:1px solid #33415f}
QWidget#titlebar{background:#0e1526;border-bottom:1px solid #2a2f45}
QLabel#titlebar_label{background:transparent;color:#f4f7ff;font-size:13px;font-weight:600}
QPushButton#titlebar_button{background:transparent;border:0;border-radius:0;color:#aeb8cd;font-size:13px;padding:0;min-height:0}
QPushButton#titlebar_button:hover{background:#1c2740;color:#f4f7ff}
QPushButton#titlebar_close{background:transparent;border:0;border-radius:0;color:#aeb8cd;font-size:13px;padding:0;min-height:0}
QPushButton#titlebar_close:hover{background:#e5484d;color:white}
QLabel#title{font-size:27px;font-weight:700} QLabel#game{background:transparent;font-size:30px;font-weight:700} QLabel#muted{background:transparent;color:#aeb8cd} QLabel#section{font-size:18px;font-weight:500}
QLabel#description{background:transparent;color:#ccd5e8;font-size:14px}
QPushButton{background:#172238;border:1px solid #33415f;border-radius:16px;padding:7px 13px;min-height:20px} QPushButton:hover{background:#22304a} QPushButton:disabled{color:#66728b}
QPushButton#primary{background:#7c5cff;color:white;border:0;font-weight:700} QPushButton#primary:hover{background:#8b70ff}
QPushButton#primary:disabled{background:#33415f;color:#7d899e}
QPushButton#nav{background:transparent;border:0;padding:8px 10px} QPushButton#nav:hover{background:#172238}
QPushButton#refresh{background:#172238;border:1px solid #33415f;border-radius:7px;padding:0;font-size:20px}
QPushButton#link{background:transparent;border:0;border-radius:0;color:#aeb8cd;font-size:12px;padding:4px 2px;min-height:0} QPushButton#link:hover{background:transparent;color:#f4f7ff}
QPushButton#danger{background:#172238;border:1px solid #e5484d;color:#ff8a8f}
QPushButton#danger:hover{background:#e5484d;color:white}
QPushButton#danger:disabled{color:#66728b;border:1px solid #33415f}
QListWidget{background:transparent;border:0;outline:0} QListWidget::item{background:#111a2c;color:#f4f7ff;border:1px solid #293650;border-radius:8px;margin:4px 0;padding:8px 10px} QListWidget::item:hover{background:#172238;color:#f4f7ff} QListWidget::item:selected{background:#172238;color:#f4f7ff;border:1px solid #7c5cff}
QTreeWidget{background:transparent;border:0;outline:0}
QTreeWidget::item{background:#111a2c;color:#f4f7ff;border:1px solid #293650;border-radius:8px;margin:2px 0;padding:8px 10px}
QTreeWidget::item:hover{background:#172238;color:#f4f7ff}
QTreeWidget::item:selected{background:#172238;color:#f4f7ff;border:1px solid #7c5cff}
QTreeWidget::item:has-children{background:transparent;border:0;color:#aeb8cd;font-weight:600;padding:8px 2px;margin:6px 0 0 0}
QTreeWidget::item:has-children:hover{background:transparent;color:#aeb8cd}
QLineEdit{background:#10182a;border:1px solid #33415f;border-radius:6px;padding:9px} QTextEdit{background:#080c17;color:#ccd5e8;border:1px solid #263552;border-radius:6px;font-family:monospace}
QComboBox{background:#10182a;border:1px solid #33415f;border-radius:0;padding:7px 32px 7px 10px;min-height:20px}
QComboBox:hover{background:#172238}
QComboBox QAbstractItemView{background:#111a2c;color:#f4f7ff;border:1px solid #33415f;outline:0;selection-background-color:#172238;selection-color:#f4f7ff;padding:4px}
QCheckBox{spacing:8px} QCheckBox::indicator{width:16px;height:16px}
QProgressBar{background:#172238;border:1px solid #33415f;border-radius:16px;min-height:20px;text-align:center;color:#f4f7ff}
QProgressBar::chunk{background:#7c5cff;border-radius:16px}
QSplitter::handle{background:#0b1020;width:12px}
QScrollBar:vertical{background:transparent;width:10px;margin:2px 2px 2px 6px}
QScrollBar::handle:vertical{background:#33415f;border-radius:4px;min-height:24px}
QScrollBar::handle:vertical:hover{background:#455172}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;background:transparent}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent}
QPushButton#store_link{background:rgba(17,26,44,150);border:1px solid rgba(124,92,255,110);border-radius:14px;color:#aeb8cd;font-size:12px;padding:6px 14px;min-height:0}
QPushButton#store_link:hover{background:rgba(23,34,56,190);color:#f4f7ff}
QWidget#setup_banner{background:rgba(124,92,255,35);border:1px solid rgba(124,92,255,110);border-radius:10px}
QLabel#setup_banner_text{background:transparent;color:#f4f7ff}
"""
