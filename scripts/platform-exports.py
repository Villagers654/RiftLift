"""Generate the native platform forwarders from dumpbin /exports output."""

import pathlib
import re
import subprocess
import sys


def generate_forwarders(symbols: str, source: str) -> str:
    overrides = set(re.findall(r"__cdecl\s+(ovr\w+)\(", source))
    names = re.findall(r"^\s+\d+\s+[\dA-F]+\s+[\dA-F]+\s+(ovr\w+)", symbols, re.M)
    if not names:
        raise ValueError("No platform exports found")
    return (
        "\n".join(
            f'#pragma comment(linker, "/export:{name}=LibOVRPlatformImpl64_1_real.{name}")'
            for name in names
            if name not in overrides
        )
        + "\n"
    )


if __name__ == "__main__":
    original, source, output = map(pathlib.Path, sys.argv[1:4])
    symbols = subprocess.check_output(["dumpbin", "/exports", str(original)], text=True)
    data = generate_forwarders(symbols, source.read_text())
    if not output.exists() or output.read_text() != data:
        output.write_text(data)
