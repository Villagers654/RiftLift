#!/usr/bin/env python3
"""Audit a release layout before it is archived.

* scan: no file name or content (ASCII and UTF-16, and zip members) holds a
  build-machine path (the checkout, the build folder, the home folder, generic
  /home/<user>/ or C:\\Users\\ style paths) or the packager's user or host name.
  Third-party files redistributed unmodified (prebuilt DLLs, license texts) are
  checked for the packager's own paths and identity only.
* imports (Windows): every DLL a PE file in the layout imports is either
  shipped beside it or guaranteed by Windows 10/11 x64 (WINDOWS_SYSTEM_DLLS).
* remap_pe_source_names: blank prebuilt __FILE__ paths (libc++abi from
  llvm-mingw) and CodeView records in a PE we built, without moving any bytes.

    release_audit.py LAYOUT [--build DIR] [--readobj llvm-readobj] [--third-party GLOB ...]
"""
from __future__ import annotations
import argparse
import fnmatch
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
# Third-party files shipped as received: never modified, and only checked for
# this machine's paths and identity (their upstream build paths are not ours).
THIRD_PARTY = ("licenses/*", "WebView2Loader.dll", "native/LibOVRPlatform64_1.dll", "native/LibOVRP2P64_1.dll",
               "native/LibOVRRT64_1.dll", "native/LibOVRPlatformImpl64_1_real.dll")


def is_third_party(name, patterns=THIRD_PARTY):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def identity(build=None):
    """Paths, account names and host names that must not ship."""
    forbidden = [str(ROOT), str(Path.home())] + ([str(Path(build).resolve())] if build else [])
    users = [os.environ.get(name, "") for name in ("USER", "LOGNAME", "USERNAME")]
    hosts = [os.environ.get("COMPUTERNAME", "")]
    try:
        hosts.append(socket.gethostname())
    except OSError:
        pass
    # Generic names are not identities (and would match ordinary words).
    generic = {"", "localhost", "root", "runner", "runneradmin", "user", "admin", "ubuntu"}
    users = sorted({u for u in users if u.lower() not in generic})
    hosts = sorted({h for h in hosts if h.lower() not in generic})
    shared = {"", "/", "\\", "/tmp", "/var/tmp"}  # a container's HOME may be a shared folder
    forbidden = sorted({f for f in forbidden if f.rstrip("/") not in shared and len(f) > 2})
    return forbidden, users, hosts


def scan(layout, forbidden, users=(), hosts=(), third_party=THIRD_PARTY):
    """Raise when a file in layout leaks a build path or the packager's identity.

    Paths match as whole absolute paths (not preceded by a path character, so
    /src does not match deps/fetch/sdl3-src/src/). Host names match as whole
    tokens; account names only in path or login context (dir/user, DOMAIN\\user,
    user@host), since short account names are often ordinary words.
    """
    own = []
    for value in forbidden:
        # A POSIX path only in its own form (\\root is a registry key, not /root).
        variants = {value} if value.startswith("/") else {value, value.replace("\\", "/"), value.replace("/", "\\")}
        for variant in variants:
            escaped = re.escape(variant.lower().encode())
            own.append(rb"(?<![a-z0-9._~-])" + escaped + rb"(?![a-z0-9._~-])")
    token = lambda value, inner: rb"(?<![a-z0-9])" + inner(re.escape(value.lower().encode())) + rb"(?![a-z0-9])"
    own += [token(value, lambda v: v) for value in hosts]
    own += [token(value, lambda v: rb"(?:(?<=[/\\@])" + v + rb"|" + v + rb"(?=[/\\@]))") for value in users]
    drive = rb"(?<![a-z0-9])[a-z]:[/\\](?![/\\])"
    generic = [drive + rb"(?:users|build|agent|workspace)[/\\]",
               rb"(?<![:/\\a-z0-9])/(?:home|users)/[^/\x00\s]+/",
               drive + rb"[^\x00\r\n]{0,512}[/\\](?:build|src|source|_work|out|\.deps)[/\\]"]
    own = [re.compile(p) for p in own]
    generic = [re.compile(p) for p in generic]

    def check(data, name, patterns):
        # Decode both UTF-16 alignments; deleting NUL bytes would join unrelated C strings.
        views = [data.lower()] + [data[offset:].decode("utf-16le", errors="replace").lower().encode("utf-8")
                                  for offset in (0, 1)]
        for view in views:
            for pattern in patterns:
                match = pattern.search(view)
                if match:
                    raise RuntimeError("Build path or identity in %s: %r" % (name, match.group()[:120]))

    for path in sorted(Path(layout).rglob("*")):
        if not path.is_file():
            continue
        name = path.relative_to(layout).as_posix()
        patterns = own if is_third_party(name, third_party) else own + generic
        check(name.encode("utf-8"), name, own + generic)
        check(path.read_bytes(), name, patterns)
        if path.suffix.lower() in (".jar", ".zip"):
            with zipfile.ZipFile(path) as archive:
                for entry in archive.infolist():
                    check(entry.filename.encode("utf-8"), name + ":" + entry.filename, own + generic)
                    check(archive.read(entry), name + ":" + entry.filename, patterns)


def remap_pe_source_names(path):
    """Blank prebuilt __FILE__ directories and CodeView records in a PE file.

    The compiler's -ffile-prefix-map cannot affect prebuilt archives (llvm-mingw's
    libc++abi); their diagnostic literals live in .rdata. Length, terminators,
    basename and every suffix offset are preserved (the linker pools suffixes).
    """
    data = bytearray(Path(path).read_bytes())
    if data[:2] != b"MZ":
        return
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    count = struct.unpack_from("<H", data, pe + 6)[0]
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    headers = pe + 24 + optional_size
    # llvm-strip keeps CodeView (PDB path) records; remove only those debug entries.
    if optional_size:
        optional = pe + 24
        magic = struct.unpack_from("<H", data, optional)[0]
        directories = optional + (112 if magic == 0x20b else 96)
        debug_rva, debug_size = struct.unpack_from("<II", data, directories + 6 * 8)
        for index in range(count):
            section = headers + index * 40
            _, virtual_address, raw_size, raw_offset = struct.unpack_from("<IIII", data, section + 8)
            if debug_rva and virtual_address <= debug_rva < virtual_address + raw_size:
                debug_offset = raw_offset + debug_rva - virtual_address
                for entry in range(debug_offset, debug_offset + debug_size, 28):
                    kind, size, _, offset = struct.unpack_from("<IIII", data, entry + 12)
                    if kind == 2:
                        data[offset:offset + size] = b"\0" * size
                        data[entry:entry + 28] = b"\0" * 28
                break
    source = re.compile(rb"(?<![a-zA-Z0-9])(?:[a-zA-Z]:[/\\](?![/\\])|/(?:home|Users)/)[ -~]+?\.(?:cpp|cc|c|h|hpp)(?=\x00)")
    for index in range(count):
        header = headers + index * 40
        if data[header:header + 8].rstrip(b"\0") != b".rdata":
            continue
        size, offset = struct.unpack_from("<II", data, header + 16)
        for match in source.finditer(bytes(data[offset:offset + size])):
            value = match.group()
            prefix = max(value.rfind(b"/"), value.rfind(b"\\")) + 1
            if prefix < 8:
                raise RuntimeError("Unexpected short source path in %s" % path)
            start = offset + match.start()
            data[start:start + prefix] = b"source/" + b"_" * (prefix - 8) + b"/"
    Path(path).write_bytes(bytes(data))


# DLLs every supported Windows 10/11 x64 installation provides (Home, Pro,
# Enterprise, Education, LTSC and the N editions). Deliberately absent: MSVC
# runtimes (vcruntime*, msvcp*, concrt*), debug CRTs, D3DX and d3dcompiler_*,
# Media Foundation (missing on N editions) and the WebView2 runtime itself.
WINDOWS_SYSTEM_DLLS = frozenset("""
advapi32 authz avrt bcrypt bcryptprimitives cabinet cfgmgr32 combase comctl32
comdlg32 credui crypt32 cryptbase cryptnet d2d1 d3d11 d3d12 d3d9 dbghelp dcomp
dhcpcsvc dinput8 dnsapi dsound dwmapi dwrite dxgi dxva2 gdi32 gdiplus glu32 hid
imagehlp imm32 iphlpapi kernel32 kernelbase ksuser mpr msimg32 msvcrt mswsock
ncrypt netapi32 normaliz ntdll ole32 oleacc oleaut32 opengl32 powrprof propsys
psapi rpcrt4 secur32 setupapi shcore shell32 shlwapi sspicli ucrtbase urlmon
user32 userenv usp10 uxtheme version winhttp wininet winmm winspool.drv
wintrust wldap32 ws2_32 wsock32 wtsapi32 xinput1_4 xinput9_1_0
""".split())
# Installed by every Vulkan-capable GPU driver.
GPU_DRIVER_DLLS = frozenset({"vulkan-1"})


def is_os_provided(name):
    lower = name.lower()
    if lower.startswith(("api-ms-win-", "ext-ms-win-")):
        return True
    stem = lower[:-4] if lower.endswith(".dll") else lower
    return stem in WINDOWS_SYSTEM_DLLS or stem in GPU_DRIVER_DLLS


def pe_imports(path, readobj):
    """The regular and delay-loaded DLL imports of one PE file."""
    result = subprocess.run([str(readobj), "--coff-imports", str(path)], check=True, capture_output=True, text=True)
    return re.findall(r"^  Name: (.+)$", result.stdout, re.M)


def import_report(layout, readobj):
    """Map each PE in layout to (imports, imports neither OS-provided nor shipped beside it)."""
    layout = Path(layout)
    shipped = {}
    for path in layout.rglob("*"):
        if path.is_file():
            shipped.setdefault(path.parent, set()).add(path.name.lower())
    report = {}
    for path in sorted(layout.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".exe", ".dll"):
            continue
        # Meta's DLLs ship as received and import what Meta's own runtime relies on.
        if is_third_party(path.relative_to(layout).as_posix()):
            continue
        imports = pe_imports(path, readobj)
        missing = [name for name in imports if not is_os_provided(name)
                   and name.lower() not in shipped.get(path.parent, ())]
        report[path.relative_to(layout).as_posix()] = (imports, missing)
    return report


def check_imports(layout, readobj):
    """Reject PE imports neither shipped nor guaranteed by Windows. Independent of the
    packaging machine: a DLL in its System32 is not evidence that users have it."""
    problems = ["%s in %s" % (name, module) for module, (_, missing) in import_report(layout, readobj).items()
                for name in missing]
    if problems:
        raise RuntimeError("Unresolved import " + "; ".join(problems))


def find_readobj(explicit=None):
    candidates = [explicit] if explicit else [
        str(ROOT / ".deps/llvm-mingw/bin/llvm-readobj.exe"), "llvm-readobj", "llvm-readobj-20", "llvm-readobj.exe"]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return path
        found = shutil.which(candidate)
        if found:
            return Path(found)
    return None


def audit(layout, build=None, readobj=None, windows=None):
    layout = Path(layout)
    forbidden, users, hosts = identity(build)
    scan(layout, forbidden, users, hosts)
    has_pe = any(p.suffix.lower() in (".exe", ".dll") for p in layout.rglob("*") if p.is_file())
    if windows or (windows is None and has_pe):
        tool = find_readobj(readobj)
        if tool is None:
            raise RuntimeError("llvm-readobj not found; pass --readobj to check the Windows imports")
        check_imports(layout, tool)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("layout", type=Path)
    parser.add_argument("--build", type=Path, help="the build folder (also a forbidden path)")
    parser.add_argument("--readobj", help="llvm-readobj for the Windows import check")
    parser.add_argument("--report", action="store_true", help="print each PE file's imports")
    args = parser.parse_args(argv)
    try:
        if args.report:
            tool = find_readobj(args.readobj)
            for module, (imports, missing) in import_report(args.layout, tool).items():
                print("%s: %s%s" % (module, ", ".join(imports), "  MISSING: " + ", ".join(missing) if missing else ""))
        audit(args.layout, args.build, args.readobj)
    except RuntimeError as error:
        raise SystemExit("release_audit: %s" % error)
    print("audit: %s is clean" % args.layout)


if __name__ == "__main__":
    sys.exit(main())
