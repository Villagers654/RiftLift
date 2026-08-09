"""Stable entry point for RiftLift's cross-platform desktop application."""

from __future__ import annotations


def main() -> int:
    try:
        from .gui_qt import main as qt_main
    except ImportError as error:
        print(f"RiftLift's GUI needs Qt 6: {error}")
        return 1
    return qt_main()


if __name__ == "__main__":
    raise SystemExit(main())
