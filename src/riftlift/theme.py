"""Shared Qt styling for RiftLift's desktop UI."""

STYLE = """
QWidget{background:#0b1020;color:#f4f7ff;font:14px sans-serif}
QLabel#title{font-size:27px;font-weight:700} QLabel#game{background:transparent;font-size:30px;font-weight:700} QLabel#muted{background:transparent;color:#aeb8cd} QLabel#section{font-size:18px;font-weight:500}
QPushButton{background:#172238;border:1px solid #33415f;border-radius:16px;padding:7px 13px;min-height:20px} QPushButton:hover{background:#22304a} QPushButton:disabled{color:#66728b}
QPushButton#primary{background:#7c5cff;color:white;border:0;font-weight:700} QPushButton#primary:hover{background:#8b70ff}
QPushButton#primary:disabled{background:#33415f;color:#7d899e}
QPushButton#nav{background:transparent;border:0;padding:8px 10px} QPushButton#nav:hover{background:#172238}
QPushButton#refresh{background:#172238;border:1px solid #33415f;border-radius:7px;padding:0;font-size:20px}
QPushButton#link{background:transparent;border:0;border-radius:0;color:#aeb8cd;font-size:12px;padding:4px 2px;min-height:0} QPushButton#link:hover{background:transparent;color:#f4f7ff}
QListWidget{background:transparent;border:0;outline:0} QListWidget::item{background:#111a2c;border:1px solid #293650;border-radius:8px;margin:4px 0;padding:8px 10px} QListWidget::item:hover{background:#172238} QListWidget::item:selected{background:#172238;border:1px solid #7c5cff}
QLineEdit{background:#10182a;border:1px solid #33415f;border-radius:6px;padding:9px} QTextEdit{background:#080c17;color:#ccd5e8;border:1px solid #263552;border-radius:6px;font-family:monospace}
QCheckBox{spacing:8px} QCheckBox::indicator{width:16px;height:16px}
QSplitter::handle{background:#0b1020;width:12px}
"""
