#!/usr/bin/env python3
r"""Turn a Windows build into RiftLift-windows64.zip and RiftLift-windows64-setup.exe.

The layout is riftlift.exe, riftlift-cli.exe, riftlift-runtime.exe and WebView2Loader.dll from the
build's bin folder, the licenses (licenses/<component>/, see
scripts/packaging/release_notices.py) and, with --native, the Windows bridge
payload (RiftLiftLauncher.exe and its DLLs, built from runtime/) in native\,
where riftlift-runtime.exe looks for it first. The zip extracts to a RiftLift folder;
the installer is built with NSIS (makensis runs on Windows and Linux). The
launcher's updater looks for RiftLift-windows64-setup.exe.

Before anything is archived the layout is audited (scripts/packaging/release_audit.py):
no build path or packager identity in any file, and every DLL import either
shipped beside the module or guaranteed by Windows 10/11.
"""
from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import release_audit  # noqa: E402
import release_notices  # noqa: E402

NSI = HERE / "riftlift.nsi"
# WebView2Loader.dll: Meta sign-in in riftlift.exe.
PROGRAMS = ("riftlift.exe", "riftlift-cli.exe", "riftlift-runtime.exe", "WebView2Loader.dll")
EXTRA = ("LICENSE", "THIRD_PARTY_NOTICES.md")
# What riftlift-runtime.exe needs in native\ (src/runtime/windows_launch.cpp).
NATIVE = ("RiftLiftLauncher.exe", "RiftLiftOpenXR64.dll", "RiftLiftOpenVR64.dll", "openvr_api64.dll",
          "RiftLiftOpenXRLayer.dll", "LibOVRPlatform64_1.dll", "LibOVRPlatformImpl64_1.dll",
          "LibOVRPlatformImpl64_1_real.dll", "LibOVRP2P64_1.dll", "LibOVRRT64_1.dll")
BUILD_LEFTOVERS = (".pdb", ".lib", ".exp", ".ilk")


def project_version():
    text = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    match = re.search(r"project\(\s*RiftLift\s+VERSION\s+(\d+(?:\.\d+){0,3})", text)
    if not match:
        raise RuntimeError("Cannot read the project version from CMakeLists.txt")
    return match.group(1)


def find_strip(explicit=None):
    candidates = [explicit] if explicit else [
        str(ROOT / ".deps/llvm-mingw/bin/llvm-strip.exe"), shutil.which("llvm-strip"), shutil.which("llvm-strip.exe")]
    return next((Path(c) for c in candidates if c and Path(c).is_file()), None)


def find_readobj(explicit=None, strip=None):
    candidates = [explicit] if explicit else [
        str(Path(strip).with_name(Path(strip).name.replace("strip", "readobj"))) if strip else None,
        str(ROOT / ".deps/llvm-mingw/bin/llvm-readobj.exe"), shutil.which("llvm-readobj"), shutil.which("llvm-readobj.exe")]
    return next((Path(c) for c in candidates if c and Path(c).is_file()), None)


def stage_layout(build, layout, native=None, strip=None, readobj=None):
    """Copy the shipped files into layout, add the licenses, audit it and
    return the relative names of its files."""
    if layout.exists():
        shutil.rmtree(layout)
    layout.mkdir(parents=True)
    for name in PROGRAMS:
        source = build / "bin" / name
        if not source.is_file():
            raise RuntimeError("Missing %s; build the windows-release preset first." % source)
        shutil.copy2(source, layout / name)
        # The build keeps debug info (RelWithDebInfo); releases ship without it.
        if strip and name.endswith(".exe"):
            subprocess.run([str(strip), "--strip-debug", str(layout / name)], check=True)
        if name.endswith(".exe"):
            release_audit.remap_pe_source_names(layout / name)
    for name in EXTRA:
        shutil.copy2(ROOT / name, layout / name)
    if native:
        missing = [name for name in NATIVE if not (native / name).is_file()]
        if missing or not list((native / "Input").glob("*.json")):
            raise RuntimeError("Incomplete native bridge in %s: %s" % (native, ", ".join(missing) or "Input/*.json"))
        shutil.copytree(native, layout / "native", ignore=lambda folder, names: [
            n for n in names if n.lower().endswith(BUILD_LEFTOVERS) or n in ("CMakeFiles", "tests")])
        # Built here (MSVC): blank absolute source paths; Meta's signed DLLs stay untouched.
        for path in sorted((layout / "native").glob("*")):
            name = "native/" + path.name
            if path.suffix.lower() in (".exe", ".dll") and not release_audit.is_third_party(name):
                release_audit.remap_pe_source_names(path)
    release_notices.stage(build, layout, "windows", native=bool(native))
    if readobj is None:
        raise RuntimeError("llvm-readobj not found; pass --readobj (it checks the release's DLL imports)")
    release_audit.scan(layout, *release_audit.identity(build))
    release_audit.check_imports(layout, readobj)
    return sorted(path.relative_to(layout).as_posix() for path in layout.rglob("*") if path.is_file())


def write_zip(layout, names, target, folder="RiftLift"):
    """Deterministic zip (sorted entries, fixed metadata) extracting to folder/."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    temporary = target.with_name(target.name + ".partial")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for name in names:
            path = layout / name
            stamp = int(epoch) if epoch else int(path.stat().st_mtime)
            info = zipfile.ZipInfo(folder + "/" + name, time.gmtime(max(stamp, 315532800))[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0x20  # FILE_ATTRIBUTE_ARCHIVE
            with path.open("rb") as source, archive.open(info, "w") as sink:
                shutil.copyfileobj(source, sink, 1 << 20)
    temporary.replace(target)


def nsis_string(value):
    if any(c in value for c in '"\r\n'):
        raise RuntimeError("Unsupported character in path: " + value)
    return value.replace("$", "$$")


def nsis_lists(layout, names, directory):
    """Write the installer's File commands and the uninstaller's removal list."""
    install = ["; Generated by build_installer.py"]
    current = None
    for name in names:
        relative = PurePosixPath(name)
        parent = "\\".join(relative.parent.parts)
        if parent != current:
            install.append('SetOutPath "$INSTDIR%s"' % nsis_string("\\" + parent if parent else ""))
            current = parent
        source = str((layout / name).resolve())
        if "${" in source or '"' in source:
            raise RuntimeError("Unsupported character in path: " + source)
        # Run-time strings escape $; compile-time source paths are literal.
        install.append('File "/oname=%s" "%s"' % (nsis_string(relative.name), source))
    directories = {PurePosixPath(name).parent for name in names} - {PurePosixPath(".")}
    for path in list(directories):
        directories.update(p for p in path.parents if p != PurePosixPath("."))
    # Files, then directories deepest first (the uninstaller removes them non-recursively).
    removal = [name.replace("/", "\\") for name in names]
    removal += ["\\".join(d.parts) + "\\" for d in sorted(directories, key=lambda d: (-len(d.parts), d.as_posix()))]
    install_path = directory / "install-files.nsh"
    list_path = directory / "uninstall.lst"
    install_path.write_text("\n".join(install) + "\n", encoding="utf-8-sig")
    list_path.write_bytes(("\r\n".join(removal) + "\r\n").encode("ascii"))
    return install_path, list_path


def find_makensis(explicit=None):
    candidates = [explicit] if explicit else [
        str(ROOT / ".deps/nsis/makensis.exe"), shutil.which("makensis"), shutil.which("makensis.exe")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise RuntimeError("makensis not found; pass --makensis (NSIS 3.x)")


def build_installer(layout, names, target, version, makensis, icon=None):
    quad = ".".join((version.split(".") + ["0"] * 4)[:4])
    size_kb = (sum((layout / n).stat().st_size for n in names) + 1023) // 1024
    environment = dict(os.environ)
    # A makensis unpacked from a distribution package keeps its data beside it.
    share = makensis.resolve().parent.parent / "share/nsis"
    if "NSISDIR" not in environment and (share / "Stubs").is_dir():
        environment["NSISDIR"] = str(share)
    with tempfile.TemporaryDirectory(prefix="riftlift-nsis-") as temporary:
        install, removal = nsis_lists(layout, names, Path(temporary))
        partial = target.with_name(target.name + ".partial")
        defines = {"VERSION": version, "VERSION_QUAD": quad, "OUTFILE": str(partial),
                   "INSTALL_FILES": str(install), "UNINSTALL_LIST": str(removal), "SIZE_KB": str(size_kb)}
        if icon:
            defines["ICON"] = str(Path(icon).resolve())
        command = [str(makensis), "-V2", "-INPUTCHARSET", "UTF8", "-WX"]
        command += ["-D%s=%s" % item for item in defines.items()] + [str(NSI)]
        subprocess.run(command, check=True, env=environment)
        partial.replace(target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", type=Path, default=ROOT / "build/windows-release", help="CMake build folder")
    parser.add_argument("--native", type=Path, help="Windows bridge payload folder (the runtime/ CMake build's bin)")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--version", default=None, help="defaults to the CMake project version")
    parser.add_argument("--makensis", help="makensis executable (default: .deps/nsis or PATH)")
    parser.add_argument("--icon", default=None,
                        help=".ico for the installer and uninstaller (default: the one the build generated)")
    parser.add_argument("--strip", help="llvm-strip (default: .deps/llvm-mingw or PATH)")
    parser.add_argument("--readobj", help="llvm-readobj for the import audit (default: beside llvm-strip, .deps/llvm-mingw or PATH)")
    parser.add_argument("--no-zip", action="store_true")
    parser.add_argument("--no-installer", action="store_true")
    args = parser.parse_args(argv)
    version = args.version or project_version()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    layout = args.output_dir / "RiftLift-win64"
    strip = find_strip(args.strip)
    names = stage_layout(args.build.resolve(), layout, args.native.resolve() if args.native else None,
                         strip, find_readobj(args.readobj, strip))
    icon = args.icon or args.build / "src/launcher/generated/launcher/riftlift.ico"
    icon = icon if Path(icon).is_file() else None
    outputs = []
    if not args.no_zip:
        outputs.append(args.output_dir / "RiftLift-windows64.zip")
        write_zip(layout, names, outputs[-1])
    if not args.no_installer:
        outputs.append(args.output_dir / "RiftLift-windows64-setup.exe")
        build_installer(layout, names, outputs[-1], version, find_makensis(args.makensis), icon)
    for path in outputs:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print("%s  %s  (%.1f MiB)" % (digest, path.name, path.stat().st_size / 1048576))


if __name__ == "__main__":
    main()
