#!/usr/bin/env python3
"""Builds assets/riftlift-icons.ttf, the icon font Linux builds embed.

The launcher names its icons by Segoe Fluent Icons code points (ui/style.h),
which Windows has as a system font. This takes the matching Lucide icons
(ISC license, https://lucide.dev) and puts each at that code point.

    pip install fonttools
    curl -LO https://registry.npmjs.org/lucide-static/-/lucide-static-1.66.0.tgz
    tar xzf lucide-static-1.66.0.tgz
    python3 scripts/make_icon_font.py package/font
"""
import argparse
import json
import pathlib

from fontTools import subset
from fontTools.ttLib import TTFont

# Segoe code point (ui/style.h icons::) -> Lucide icon name.
ICONS = {
    0xE8F1: "library-big",       # library
    0xE896: "download",          # download
    0xE713: "settings",          # settings
    0xE77B: "user",              # account
    0xF5B0: "play",              # play
    0xE721: "search",            # search
    0xE72C: "refresh-cw",        # refresh
    0xE72B: "arrow-left",        # back
    0xE8B7: "folder",            # folder
    0xE74D: "trash-2",           # trash
    0xE712: "ellipsis",          # more
    0xE711: "x",                 # cancel
    0xE769: "pause",             # pause
    0xE768: "play",              # resume
    0xE946: "info",              # info
    0xE73E: "check",             # check
    0xE895: "circle-arrow-down", # update
    0xE7BA: "triangle-alert",    # warning
    0xF3B1: "log-out",           # signout
    0xE774: "globe",             # globe
    0xE76C: "chevron-right",     # chevron_right
    0xE76B: "chevron-left",      # chevron_left
    0xE8CB: "arrow-down-up",     # sort
    0xE72E: "lock",              # lock
    0xE70D: "chevron-down",      # chevron_down
    0xE70E: "chevron-up",        # chevron_up
    0xE710: "plus",              # add
    0xE734: "star",              # star
    0xE8AB: "arrow-left-right",  # swap
    0xE7B8: "package",           # package
    0xE753: "cloud",             # cloud
    0xEA86: "puzzle",            # puzzle
    0xE783: "circle-x",          # error
    0xE7FC: "gamepad-2",         # game
    0xE716: "users",             # people
    0xE74E: "save",              # save
}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("lucide", type=pathlib.Path, help="lucide-static's font folder")
    parser.add_argument("--out", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[1] / "assets" / "riftlift-icons.ttf")
    args = parser.parse_args()

    font = TTFont(args.lucide / "lucide.ttf")
    by_name = json.loads((args.lucide / "codepoints.json").read_text())
    missing = sorted(set(ICONS.values()) - by_name.keys())
    if missing:
        raise SystemExit(f"Not in this Lucide font: {', '.join(missing)}")

    options = subset.Options()
    options.glyph_names = False
    options.name_IDs = [0, 1, 2, 3, 4, 5, 6]  # names and the copyright notice
    options.notdef_outline = True
    sub = subset.Subsetter(options)
    sub.populate(unicodes=sorted({by_name[name] for name in ICONS.values()}))
    sub.subset(font)

    glyphs = font.getBestCmap()
    remapped = {segoe: glyphs[by_name[name]] for segoe, name in ICONS.items()}
    for table in font["cmap"].tables:
        if table.isUnicode():
            table.cmap = dict(remapped)
    font["cmap"].tables = [t for t in font["cmap"].tables if t.isUnicode()]
    font["name"].setName("RiftLift Icons", 1, 3, 1, 0x409)
    font["name"].setName("RiftLift Icons", 4, 3, 1, 0x409)
    font["name"].setName("RiftLiftIcons", 6, 3, 1, 0x409)
    font.save(args.out)
    print(f"Wrote {len(ICONS)} icons to {args.out} ({args.out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
