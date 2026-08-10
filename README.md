# RiftLift

**Play your Meta Rift PC VR games on Linux.**

RiftLift gives you one desktop app to sign in to Meta, download games you own,
add them to Steam, and launch them through your existing OpenXR headset setup.
If your headset works with Monado, SteamVR is not required.

![RiftLift showing an installed Meta Rift library](docs/images/riftlift-library.png)

> RiftLift is an early release. Many games work, but some titles may still need
> compatibility fixes.

## Quick start

Before you start, you need:

- a 64-bit Linux PC;
- Steam;
- a VR headset that already works through Monado/OpenXR; and
- a Meta account that owns a **Rift / PC VR** game.

Your headset must already run OpenXR apps. RiftLift does not install headset
drivers or Monado.

### 1. Install RiftLift

Open a terminal and paste:

```bash
git clone https://github.com/Villagers654/RiftLift.git
cd RiftLift
./install.sh
```

The first install can take a while while RiftLift downloads its shared
compatibility files. It will add **RiftLift** to your application menu.

### 2. Check your setup and sign in

Open **RiftLift** from your application menu and click **System**. If something
is not ready, open **View Activity** for the details.

Click **Sign In** and complete Meta's normal sign-in flow. RiftLift keeps that
session for future downloads; if it expires, use **Sign In** again.

### 3. Add a game

Copy the URL of a game you own from the Meta **Rift / PC VR** store. Click
**Add Game** in RiftLift and paste it.

![RiftLift Add Game window](docs/images/riftlift-add-game.png)

Leave **Add to Steam when finished** checked and click **Install**. Use
**View Activity** if you want to watch the download.

> A Quest-only purchase is not a Windows PC game. The store page must offer a
> Rift or PC VR build. Cross-buy titles work when the PC version is present on
> your Meta account.

### 4. Play

Select the game in RiftLift and click **Launch in VR**. You can also launch its
new shortcut from Steam or from a headset dashboard that reads your Steam VR
library, such as WayVR.

## Everyday use

The desktop app is the recommended way to use RiftLift:

- **System** checks whether RiftLift and your OpenXR setup are ready.
- **Sign In** opens Meta's sign-in flow.
- **Add Game** downloads an owned Rift game and optionally adds it to Steam.
- **Launch in VR** starts the selected game through your active OpenXR runtime.
- The **⟳** button reloads games added elsewhere and refreshes store details
  and artwork.
- **View Activity** shows download, setup, launch, and diagnostic messages.

Steam may restart once when RiftLift adds or updates shortcuts.

## Troubleshooting

Start with **System** in the desktop app, or run `riftlift doctor`.

Common fixes:

- **RiftLift cannot find OpenXR:** start your normal Monado setup and try again.
- **Meta asks you to sign in again:** click **Sign In**, finish Meta's flow, and
  retry.
- **A game is missing from Steam:** close Steam, run `riftlift steam-sync`, and
  reopen it.
- **A game fails to launch:** run **System**, then retry from a terminal with
  `RIFTLIFT_PROTON_LOG=1 riftlift launch GAME-SLUG`.

For a nonstandard Monado manifest, set
`XR_RUNTIME_JSON=/path/to/openxr_monado.json` before opening RiftLift. If your
headset setup needs a special start command, set
`RIFTLIFT_LAUNCH_WRAPPER='your-runtime-start-wrapper'`.

## Updating RiftLift

From the RiftLift folder, run:

```bash
git pull --ff-only
./install.sh
```

Your sign-in and installed games are preserved.

## Command line (optional)

Everything in the desktop app is also available from the command line:

```bash
riftlift doctor
riftlift login
riftlift add 'https://www.meta.com/experiences/APP_ID/'
riftlift list
riftlift launch GAME-SLUG
```

Useful maintenance commands:

```bash
riftlift steam-sync
riftlift metadata
riftlift metadata GAME-SLUG --refresh
riftlift setup
```

Run `riftlift --help` or `riftlift COMMAND --help` for every option. Unusual
Rift Store manifests can use `riftlift add --executable PATH` and
`--arguments '...'`, but normal games should not need either override.
Download concurrency adapts to the CPUs available to RiftLift; use `--jobs` only
when you want to override it.

## Steam games with an Oculus mode (advanced)

Some Steam games include an Oculus mode even though they were not downloaded
from the Meta store. Install those games normally in Steam. RiftLift can detect
64-bit Unity, Unreal, and native Oculus SDK games with:

```bash
riftlift steam-oculus-ids
```

A headset integration can route those Steam app IDs through:

```bash
riftlift launch-steam APP_ID -- %command%
```

This keeps Steam ownership, updates, achievements, artwork, and its normal Play
button. If a game offers both Oculus and SteamVR/OpenVR modes, RiftLift respects
the mode selected in Steam instead of forcing Oculus mode.

For these games, RiftLift reads the title, description, developer, genres, and
official library artwork from the Steam catalog instead of treating the Steam
app ID as a Meta store ID. The library **⟳** button updates that cached Steam
data and portrait artwork at any time.

RiftLift does not keep a list of specially supported titles. It uses the
game's own manifest, engine layout, executable format, and Steam launch command
to find the correct 64-bit game executable and arguments.

## Star history

[![Star History Chart](https://api.star-history.com/svg?repos=Villagers654/RiftLift&type=Date)](https://star-history.com/#Villagers654/RiftLift&Date)

## How RiftLift works

RiftLift keeps its files under `~/.local/share/riftlift` and uses one reusable
Proton environment. It combines:

- the maintained [RiftLift Revive](https://github.com/Villagers654/Revive) fork;
- GE-Proton and WineOpenXR;
- the parts of Meta's PC client needed for account sign-in;
- the entitlement-respecting
  [meta-pcvr-downloader](https://github.com/Villagers654/meta-pcvr-downloader);
  and
- a small compatibility bridge for older Oculus Platform SDK games.

The rendering path is:

```text
Rift game -> RiftLift ReviveXR -> GE-Proton WineOpenXR -> your OpenXR runtime -> headset
```

RiftLift uses Meta's entitlement service before downloading a game. The legacy
Platform SDK bridge supplies local login and entitlement responses only for a
download whose ownership was already verified; other SDK calls continue to
Meta's original library. RiftLift is compatibility software, not a purchase or
DRM bypass.

Headset drivers, Monado, compositor lifecycle, and device-specific setup remain
the responsibility of the host VR setup. See
[Architecture and ownership](docs/ARCHITECTURE.md) for the technical boundary.

## Legal and security

RiftLift is unaffiliated with Meta, Oculus, Valve, Collabora, or Sony. You must
own the games you download. Its cached Meta runtime token is readable only by
your user and is never included in diagnostic output. Downloads are pinned and
verified before extraction, archive paths are validated, and Steam's shortcut
file is backed up before an atomic replacement.

RiftLift is GPL-3.0-or-later. Bundled and upstream components retain their own
licenses and notices.
