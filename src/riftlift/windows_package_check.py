"""Bounded, offscreen verification of the built Windows application."""

from __future__ import annotations

import json
import os
from pathlib import Path


def run(output: Path) -> int:
    # Never opens or drives the user's desktop. Uses the shipped Qt widgets.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6 import QtCore, QtGui, QtWidgets

    from .config import Paths, games
    from .gui import main
    from .windows import FILES, PLATFORM_FILES, runtime_dir

    output.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication([])
    # Offscreen Qt on Windows does not automatically discover system fonts.
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("segoeui.ttf", "segoeuib.ttf"):
        QtGui.QFontDatabase.addApplicationFont(str(fonts / name))
    result = {}

    def finish():
        try:
            window = app._riftlift_window
            if not window.grab().save(str(output / "library.png")):
                raise OSError("Could not save the library render")
            paths = Paths.defaults()
            native = runtime_dir(paths)
            missing = sorted(
                name for name in FILES | PLATFORM_FILES if not (native / name).is_file()
            )
            result.update(
                success=not missing,
                native=str(native),
                missing=missing,
                games=len(games(paths)),
                activations=getattr(app, "_riftlift_activations", 0),
                width=window.width(),
                height=window.height(),
                icon_loaded=not app.windowIcon().isNull(),
            )
        except Exception as error:
            result.update(success=False, error=str(error))
        (output / "result.json").write_text(json.dumps(result, indent=2))
        app.quit()

    QtCore.QTimer.singleShot(5000, finish)
    main()
    return 0 if result.get("success") else 1
