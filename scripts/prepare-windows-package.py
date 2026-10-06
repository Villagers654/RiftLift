"""Validate the native payload and derive the Windows icon from our SVG."""

import os
import sys
from pathlib import Path

from riftlift.util import sha256
from riftlift.windows import FILES, PLATFORM_FILES, SDK_RUNTIME_SHA256

runtime, icon = map(Path, sys.argv[1:])
missing = sorted(
    name for name in FILES | PLATFORM_FILES if not (runtime / name).is_file()
)
if missing:
    raise SystemExit("Incomplete native runtime: " + ", ".join(missing))
if not list((runtime / "Input").glob("*.json")):
    raise SystemExit("Missing native controller bindings")
if sha256(runtime / "LibOVRRT64_1.dll") != SDK_RUNTIME_SHA256:
    raise SystemExit("The signed SDK discovery DLL does not match the pinned version")

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PIL import Image  # noqa: E402
from PySide6 import QtCore, QtGui, QtSvg  # noqa: E402

app = QtGui.QGuiApplication([])
svg = Path(__file__).resolve().parents[1] / "assets/io.github.villagers654.RiftLift.svg"
renderer = QtSvg.QSvgRenderer(str(svg))
image = QtGui.QImage(256, 256, QtGui.QImage.Format_RGBA8888)
image.fill(QtCore.Qt.transparent)
painter = QtGui.QPainter(image)
renderer.render(painter)
painter.end()
icon.parent.mkdir(parents=True, exist_ok=True)
png = icon.with_suffix(".png")
image.save(str(png))
Image.open(png).save(
    icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)]
)
