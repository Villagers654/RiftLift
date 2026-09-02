"""Stable entry point for RiftLift's desktop application."""

from __future__ import annotations


def main() -> int:
    try:
        from .main_window import main as window_main
    except ModuleNotFoundError as error:
        if not error.name or not error.name.startswith("PySide6"):
            raise
        print(f"RiftLift's GUI needs Qt 6: {error}")
        return 1
    return window_main()


if __name__ == "__main__":
    raise SystemExit(main())
