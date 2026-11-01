#!/usr/bin/env python3
"""Fetch the pinned Windows toolchain the windows-release preset expects.

Unpacks llvm-mingw, CMake and Ninja into .deps/ (each checked against its
SHA-256), then: .deps/cmake/bin/cmake --preset windows-release
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / ".deps"
TOOLS = {
    "llvm-mingw": ("https://github.com/mstorsjo/llvm-mingw/releases/download/20260922/"
                   "llvm-mingw-20260922-ucrt-x86_64.zip",
                   "e3ad77d117a4bea19a7a3b333341824d79a5a371004a10e25b8504e7b3047666",
                   "llvm-mingw-20260922-ucrt-x86_64"),
    "cmake": ("https://github.com/Kitware/CMake/releases/download/v4.4.3/cmake-4.4.3-windows-x86_64.zip",
              "4d52ebab7193a698651639ed80d8d04fd903358843572cf44c7fd234cb7c26ab",
              "cmake-4.4.3-windows-x86_64"),
    "ninja": ("https://github.com/ninja-build/ninja/releases/download/v1.13.2/ninja-win.zip",
              "07fc8261b42b20e71d1720b39068c2e14ffcee6396b76fb7a795fb460b78dc65",
              ""),
}


def fetch(name, url, sha256, strip):
    target = DEPS / name
    stamp = target / ".sha256"
    if stamp.is_file() and stamp.read_text().strip() == sha256:
        return
    print("Fetching", url, flush=True)
    with tempfile.TemporaryDirectory(dir=DEPS) as temporary:
        archive = Path(temporary) / "archive.zip"
        with urllib.request.urlopen(url) as response, archive.open("wb") as sink:
            shutil.copyfileobj(response, sink, 1 << 20)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != sha256:
            raise SystemExit("Checksum mismatch for " + url)
        unpacked = Path(temporary) / "unpacked"
        with zipfile.ZipFile(archive) as zipped:
            for member in zipped.namelist():
                if member.startswith("/") or ".." in Path(member).parts:
                    raise SystemExit("Unsafe path in " + url + ": " + member)
            zipped.extractall(unpacked)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(unpacked / strip if strip else unpacked), target)
    stamp.write_text(sha256 + "\n")


def main():
    if sys.platform != "win32":
        raise SystemExit("These are Windows tools; on Linux use the linux-release preset.")
    DEPS.mkdir(exist_ok=True)
    for name, (url, sha256, strip) in TOOLS.items():
        fetch(name, url, sha256, strip)


if __name__ == "__main__":
    main()
