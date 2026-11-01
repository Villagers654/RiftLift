#!/usr/bin/env python3
"""Copy the license notices of everything linked into the release binaries.

Writes <layout>/licenses/<component>/... from the fetched dependency sources
(FETCHCONTENT_BASE_DIR, or FETCHCONTENT_SOURCE_DIR_<NAME> overrides, read from
the build's CMakeCache.txt), the toolchain and scripts/packaging/release-notices.
A required file that is missing fails packaging.

    release_notices.py --build BUILD --layout LAYOUT [--platform linux|windows] [--native]

--native adds the notices of the Windows bridge payload in native\\
(runtime/, see scripts/packaging/windows/build_installer.py).
"""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
NOTICE_DIR = Path(__file__).resolve().parent / "release-notices"

# FetchContent component -> license files, for what src/*/CMakeLists.txt links into
# riftlift, riftlift-cli and riftlift-runtime (not doctest: tests only; not the
# fetched zlib: built but never linked; FreeType inflates with its bundled copy).
NOTICES = {
    "nlohmann_json": ["LICENSE.MIT"],          # riftlift_common
    "curl": ["COPYING"],                        # riftlift_common (static libcurl)
    "miniz": ["LICENSE"],                       # riftlift_common, riftlift_runtime_core
    "sdl3": ["LICENSE.txt", "src/hidapi/LICENSE.txt", "src/hidapi/LICENSE-bsd.txt"],  # riftlift (SDL_HIDAPI on)
    "imgui": ["LICENSE.txt"],                   # riftlift_imgui
    "freetype": ["LICENSE.TXT", "docs/FTL.TXT"],  # riftlift_imgui
    "webp": ["COPYING", "PATENTS"],             # webpdecoder
    "stb": ["LICENSE"],                         # stb_image (riftlift_common, riftlift)
    "nanosvg": ["LICENSE.txt"],                 # riftlift (logo)
}
# FreeType sources whose own permission notices are compiled into libfreetype.
FREETYPE_NOTICES = ("src/bdf/bdflib.c", "src/pcf/pcfread.c", "src/base/fthash.c", "src/autofit/ft-hb-ft.c")
WINDOWS_NOTICES = {
    # WebView2Loader.dll (Meta sign-in), redistributed from the NuGet package.
    "webview2": ["LICENSE.txt", "NOTICE.txt"],
}
# Statically linked by llvm-mingw: libc++, libc++abi, libunwind and the mingw-w64 runtime.
LLVM_MINGW_NOTICES = ("mingw-COPYING", "mingw-COPYING.MinGW-w64-runtime.txt",
                      "mingw-COPYING.MinGW-w64.txt", "mingw-COPYING.winpthreads.txt")


def copy(source, target):
    if not Path(source).is_file():
        raise RuntimeError("Missing license file: %s" % source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def cache_entries(build):
    cache = build / "CMakeCache.txt"
    if not cache.is_file():
        raise RuntimeError("%s has no CMakeCache.txt" % build)
    entries = {}
    for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
        key, separator, value = line.partition("=")
        if separator and not key.startswith(("//", "#")):
            entries[key.partition(":")[0]] = value
    return entries


def source_dir(entries, component):
    override = entries.get("FETCHCONTENT_SOURCE_DIR_" + component.upper())
    if override:
        return Path(override)
    base = entries.get("FETCHCONTENT_BASE_DIR") or str(ROOT / ".deps/fetch")
    return Path(base) / (component + "-src")


def comment_notice(path, *words):
    """The first /* ... */ block of a source file holding all the given words."""
    text = Path(path).read_text(encoding="utf-8", errors="replace") if Path(path).is_file() else ""
    for block in re.findall(r"/\*.*?\*/", text, re.S):
        if all(word in block for word in words):
            return block + "\n"
    raise RuntimeError("No license notice found in %s" % path)


def static_runtime_notices(entries, licenses):
    """System libraries linked statically by cmake/toolchains/linux-baseline.cmake
    (OpenSSL, libc++/libc++abi, libgcc/libatomic), from the packaging host's
    package documentation: run this in the baseline container that built them."""
    crypto = entries.get("OPENSSL_CRYPTO_LIBRARY", "")
    if crypto.endswith(".a"):
        candidates = [Path("/usr/share/doc") / name / "copyright" for name in ("libssl-dev", "libssl3", "openssl")]
        candidates.append(Path(crypto).parent.parent / "share/doc/openssl/LICENSE.txt")
        source = next((p for p in candidates if p.is_file()), None)
        if source is None:
            raise RuntimeError("OpenSSL is linked statically but its license was not found")
        copy(source, licenses / "openssl" / ("LICENSE.txt" if source.suffix == ".txt" else "copyright"))
    flags = " ".join(entries.get(key, "") for key in ("CMAKE_EXE_LINKER_FLAGS", "CMAKE_CXX_FLAGS"))
    if "-static-libstdc++" in flags and "-stdlib=libc++" in flags:
        copy(NOTICE_DIR / "llvm/LICENSE.txt", licenses / "llvm-runtime/LICENSE.txt")
        found = False
        for package in ("libc++", "libc++abi"):
            docs = sorted(Path("/usr/share/doc").glob(package + "-*-dev/copyright"))
            if docs:
                copy(docs[-1], licenses / "llvm-runtime" / (package + "-copyright"))
                found = True
        if not found:
            raise RuntimeError("libc++ is linked statically but its package copyright was not found")
    if "-static-libgcc" in flags:
        # GCC runtime library exception: no notice is required; ship it when present.
        for package in ("libgcc-11-dev", "gcc-11-base", "libatomic1"):
            path = Path("/usr/share/doc") / package / "copyright"
            if path.is_file():
                copy(path, licenses / "gcc-runtime" / (package + "-copyright"))
                break


def native_notices(licenses):
    """The Windows bridge in native\\: Revive and what runtime/CMakeLists.txt links
    (OpenVR, Detours, OpenXR loader, CImg, ...) are listed in runtime/LICENSE,
    which the native build also copies to native\\LICENSE."""
    copy(ROOT / "runtime/LICENSE", licenses / "riftlift-native/LICENSE")
    externals = ROOT / "runtime/Externals"
    for name in ("LICENSE.md", "LICENSE.txt"):
        if (externals / "Vulkan" / name).is_file():
            copy(externals / "Vulkan" / name, licenses / "vulkan-headers" / name)
            break
    else:
        raise RuntimeError("Missing license file: %s" % (externals / "Vulkan/LICENSE.md"))
    (licenses / "glad").mkdir(parents=True, exist_ok=True)
    (licenses / "glad/khrplatform.txt").write_text(
        comment_notice(externals / "glad/include/KHR/khrplatform.h", "Copyright", "Permission"), encoding="utf-8")


def stage(build, layout, platform, native=False):
    build, layout = Path(build), Path(layout)
    entries = cache_entries(build)
    licenses = layout / "licenses"
    if licenses.exists():
        shutil.rmtree(licenses)
    copy(ROOT / "LICENSE", licenses / "riftlift/LICENSE")
    copy(ROOT / "THIRD_PARTY_NOTICES.md", licenses / "THIRD_PARTY_NOTICES.md")
    components = dict(NOTICES)
    if platform == "windows":
        components.update(WINDOWS_NOTICES)
    for component, files in components.items():
        root = source_dir(entries, component)
        for name in files:
            copy(root / name, licenses / component / name)
    freetype = source_dir(entries, "freetype")
    copy(NOTICE_DIR / "freetype/NOTICE.txt", licenses / "freetype/NOTICE.txt")
    for relative in FREETYPE_NOTICES:
        (licenses / "freetype" / (Path(relative).stem + ".txt")).write_text(
            comment_notice(freetype / relative, "Copyright", "Permission"), encoding="utf-8")
    # FreeType's gzip module inflates with its bundled zlib (FT_DISABLE_ZLIB).
    (licenses / "zlib").mkdir(parents=True, exist_ok=True)
    (licenses / "zlib/LICENSE").write_text(
        comment_notice(freetype / "src/gzip/zlib.h", "Copyright", "Permission"), encoding="utf-8")
    if platform == "windows":
        # llvm-mingw's root, from the compiler (or, with a toolchain file, the linker) the build used.
        tools = [Path(entries[key]) for key in ("CMAKE_CXX_COMPILER", "CMAKE_LINKER", "CMAKE_AR") if entries.get(key)]
        roots = [tool.parent.parent for tool in tools] + [ROOT / ".deps/llvm-mingw"]
        toolchain = next((root for root in roots if (root / "LICENSE.TXT").is_file()), roots[0])
        copy(toolchain / "LICENSE.TXT", licenses / "llvm-runtime/LICENSE.TXT")
        for name in LLVM_MINGW_NOTICES:
            copy(NOTICE_DIR / "llvm" / name, licenses / "llvm-runtime" / name)
        if native:
            native_notices(licenses)
    else:
        static_runtime_notices(entries, licenses)
    return sorted(p.relative_to(layout).as_posix() for p in licenses.rglob("*") if p.is_file())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--platform", choices=("linux", "windows"),
                        default="windows" if sys.platform == "win32" else "linux")
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args(argv)
    try:
        names = stage(args.build, args.layout, args.platform, args.native)
    except RuntimeError as error:
        raise SystemExit("release_notices: %s" % error)
    print("licenses: %d files" % len(names))


if __name__ == "__main__":
    main()
