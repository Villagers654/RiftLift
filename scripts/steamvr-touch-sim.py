"""Drive the test-only simulated Touch controllers (runtime/tests/steamvr-touch-sim).

  python scripts/steamvr-touch-sim.py register <driver-folder>
  python scripts/steamvr-touch-sim.py unregister <driver-folder>
  python scripts/steamvr-touch-sim.py press right trigger [--value 1] [--hold 1.5]
  python scripts/steamvr-touch-sim.py set left stickx -1

Registering changes SteamVR's driver list for this user; restart SteamVR after
registering and unregister when done.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

COMMANDS = Path(os.environ["LOCALAPPDATA"]) / "RiftLift" / "touchsim.txt"


def steamvr_root() -> Path:
    registry = Path(os.environ["LOCALAPPDATA"]) / "openvr" / "openvrpaths.vrpath"
    for root in json.loads(registry.read_text(encoding="utf-8"))["runtime"]:
        if (Path(root) / "bin/win64/vrpathreg.exe").is_file():
            return Path(root)
    raise SystemExit("SteamVR is not registered for this user")


def send(lines: list[str]) -> None:
    COMMANDS.parent.mkdir(parents=True, exist_ok=True)
    # The driver polls every 100 ms and deletes the file after reading it.
    deadline = time.monotonic() + 5
    while COMMANDS.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    temporary = COMMANDS.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(COMMANDS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("register", "unregister"):
        sub.add_parser(name).add_argument("driver", type=Path)
    press = sub.add_parser("press")
    setter = sub.add_parser("set")
    for command in (press, setter):
        command.add_argument("hand", choices=["left", "right", "both"])
        command.add_argument("input")
    press.add_argument("--value", type=float, default=1.0)
    press.add_argument("--hold", type=float, default=0.3)
    setter.add_argument("value", type=float)
    args = parser.parse_args()

    if args.command in {"register", "unregister"}:
        verb = "adddriver" if args.command == "register" else "removedriver"
        tool = steamvr_root() / "bin/win64/vrpathreg.exe"
        subprocess.run([str(tool), verb, str(args.driver.resolve())], check=True)
    elif args.command == "set":
        send([f"{args.hand} {args.input} {args.value}"])
    else:
        send([f"{args.hand} {args.input} {args.value}"])
        time.sleep(args.hold)
        send([f"{args.hand} {args.input} 0"])


if __name__ == "__main__":
    main()
