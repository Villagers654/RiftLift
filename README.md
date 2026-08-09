# RiftLift

RiftLift brings owned Meta Rift PC games into standards-based Linux OpenXR. It
combines maintained Revive and xrizer forks, GE-Proton, WineOpenXR, Meta's PC
client/runtime, and the entitlement-respecting
[meta-pcvr-downloader](https://github.com/Villagers654/meta-pcvr-downloader)
behind one small command-line interface. The primary path targets Monado and
does not require SteamVR.

> Early release: the compatibility foundation is usable, but individual games
> can still expose Windows assumptions. Please report the game and attach the
> output from `riftlift doctor`.

## Install

Requirements: an x86-64 Linux desktop, Steam, a working OpenXR runtime such as
Monado, Python 3.10+, and roughly 1 GiB for compatibility tools (plus games).

```bash
git clone https://github.com/Villagers654/RiftLift.git
cd RiftLift
./install.sh
```

The installer creates an isolated environment under
`~/.local/share/riftlift`, installs the current GE-Proton and compatibility
payload, and creates one reusable Proton prefix. It does not modify a system
Wine installation.

It also installs a native Qt 6 RiftLift desktop app in your application menu. Open
it there or run `riftlift gui` (equivalently, `riftlift-gui`) to browse your
library, add games, launch them, refresh artwork, synchronize Steam, and check
the compatibility stack without using the command line.

The GUI supports current mainstream Linux distributions and Windows 10 or
newer. Game installation and launch remain Linux-only because they require
Proton and a Linux OpenXR runtime; Windows opens in clearly labeled library
mode.

## Sign in once

```bash
riftlift login
```

Sign in to Meta in the Horizon Link window and complete its browser handoff.
The Meta client, OAF session database, and browser login live in persistent
locations, so you never paste a token or repeat login for every game. RiftLift
caches the scoped runtime access token created by Horizon Link in a private
mode-0600 file and refreshes it from that persistent client profile. It does not
scrape an unrelated browser profile. If Meta expires the session, run
`riftlift login` again.

## Download a Rift game and add it to Steam

Copy the URL of an owned PC VR/Rift game from the Meta store, then run:

```bash
riftlift add 'https://www.meta.com/experiences/2031736060288351/'
```

RiftLift will:

1. verify the account's entitlement with Meta;
2. download the newest PC build with per-segment and per-file SHA-256 checks;
3. save the game's manifest and compatibility record;
4. safely add a tagged VR shortcut to Steam; and
5. install cover, hero, logo, icon, description, developer, publisher, and genre
   metadata for Steam and WayVR; and
6. restart Steam once if it was open, preventing it from overwriting the edit.

Start the game from Steam like any other VR title, or run
`riftlift launch GAME-SLUG`. Future Rift titles use the same command and shared
prefix. Use `riftlift list` to see their slugs.

RiftLift reads public JSON-LD metadata from the title's official Meta store
page and keeps a persistent local copy. Run `riftlift metadata` to backfill
older installs or `riftlift metadata GAME-SLUG --refresh` to refresh it. Steam
shortcut IDs are preserved across updates, so custom artwork and controller
layouts do not become detached when RiftLift itself moves or updates.

For unusual manifests, `--executable PATH` and `--arguments '...'` provide an
explicit escape hatch. `riftlift add --help` documents all options.

## How it works

The default rendering path is:

```text
Rift game -> RiftLift ReviveXR -> GE-Proton WineOpenXR -> active Linux OpenXR runtime -> headset
```

The bundled legacy Platform SDK bridge supplies local login/entitlement
responses only after the downloader has verified ownership. Unhandled Platform
SDK functions and messages forward to Meta's original DLL. This is a Wine
compatibility fix, not DRM or purchase bypass tooling.

The maintained forks are:

- [RiftLift Revive](https://github.com/Villagers654/Revive), with WineOpenXR,
  Vulkan swapchain, session lifecycle, and Monado fixes.
- [RiftLift xrizer](https://github.com/Villagers654/xrizer), with Linux loader
  fallback and OpenXR stage-bound chaperone support for OpenVR fallbacks.

## Troubleshooting

```bash
riftlift doctor
```

This verifies the active OpenXR runtime, Steam, GE-Proton, ReviveXR, Meta
client, platform bridge, and every registered executable without printing
account secrets. Set `RIFTLIFT_PROTON_LOG=1` for a Proton log. Set
`RIFTLIFT_LAUNCH_WRAPPER='your-runtime-start-wrapper'` when a headset integration
must start or hand off its compositor before the game. If
`psvr2-fossvr-run` is installed, RiftLift discovers it automatically.

## Updating

Pull a tagged release and rerun `./install.sh`. Setup is idempotent: pinned,
checksum-verified components are reused, the login prefix and installed games
are preserved, and incompatible partial downloads are rejected.

## Legal and security

RiftLift is unaffiliated with Meta, Oculus, Valve, Collabora, or Sony. You must
own the games you download. The cached runtime token is readable only by your
user and is never included in diagnostic output. Runtime archives are pinned and verified before
extraction, archive paths are validated, and Steam's shortcut file is backed up
before an atomic replacement.

RiftLift is GPL-3.0-or-later. Bundled/upstream components keep their own license
and notice files.
