# Build with scripts/build-windows.ps1 after building the native x64 runtime.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH).parent
native = Path(os.environ["RIFTLIFT_PACKAGE_RUNTIME"]).resolve()
icon = Path(os.environ["RIFTLIFT_PACKAGE_ICON"]).resolve()
a = Analysis(
    [str(root / "scripts/windows-entry.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(native), "native"), (str(icon), "assets")]
    + collect_data_files("meta_pcvr_downloader")
    + copy_metadata("meta-pcvr-downloader"),
    hiddenimports=["PySide6.QtSvg", "PySide6.QtNetwork"],
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="RiftLift",
    console=False, icon=str(icon), upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="RiftLift", upx=False)
