<div align="center">

<img src="docs/media/logo.svg" width="72" alt="">

# RiftLift

**Your Meta Rift library, on any PC VR headset.**

Download and play the Rift / PC VR games you own on Windows and Linux, through SteamVR or any OpenXR runtime.<br>
No Meta PC app. Free and open source.

[**Download**](https://github.com/Villagers654/RiftLift/releases/latest) · [Compatibility](docs/COMPATIBILITY.md) · [Report an issue](https://github.com/Villagers654/RiftLift/issues)

<img src="docs/media/launcher.webp" width="760" alt="The RiftLift launcher: a library of Rift games, from Lone Echo and Stormland to Steam games with an Oculus mode">

</div>

## Why RiftLift

- **Any headset.** Rift games run through the VR setup you already use: SteamVR, or an OpenXR runtime such as Monado or WiVRn.
- **Linux too.** Games run under GE-Proton, which RiftLift downloads the first time you play; see the [compatibility wiki](docs/COMPATIBILITY.md).
- **Your whole library.** Sign in with up to four Meta accounts; their games appear together, with your Steam games that have an Oculus mode.
- **Simple.** Sign in with Meta, install, press Play. Your games can show up in your Steam library too.

<img src="docs/media/game.webp" width="760" alt="Lone Echo's page in RiftLift: installed, with Play, its details and playtime">

### Install and play

1. Download `RiftLift-windows64-setup.exe` from [Releases](https://github.com/Villagers654/RiftLift/releases/latest) and run it.
   On Linux, install the package for your distribution (`.deb`, `.rpm`, AUR `riftlift-bin`, AppImage or Flatpak), or extract the `.tar.gz` anywhere.
2. Start RiftLift and choose **Sign in** and use the account that owns your Rift games.
3. Pick a game in your library and choose **Install**.
4. Start your headset's runtime, then choose **Play**.

### Games from somewhere else

The **Add Game** page adds games that aren't in your Meta PC library:

- **From this PC:** choose a Rift game's `.exe` (or drop it on the window). RiftLift launches it where it is.
- **Steam games with an Oculus mode:** choose **Find games**. Steam still owns and updates them.
- **By store link:** paste the Rift store link of a cross-buy game Meta leaves out of your PC library.

To keep an older build or a beta beside the one you have, choose **… → Install another version…** on the game's page.

### Play from Steam

Turn on **Add games to Steam** in Settings. Installed games appear in Steam and SteamVR the next time Steam starts.

### Something wrong?

If a game crashes, click **Report issue** on the banner that appears. Otherwise, use **Upload debug report** in Settings. Either way, RiftLift collects the launcher and game logs from your last launch into one debug report, uploads it as an unlisted paste that expires after 90 days, and gives you a link to copy. If the upload fails, attach the report file instead (**Show file**).

[Open an issue](https://github.com/Villagers654/RiftLift/issues) with the game, what happened, how to make it happen again and the debug report link. To watch a game's output live, turn on **Show runtime console** in Settings.

### Build (Windows)

```sh
python scripts/bootstrap-windows.py          # pinned llvm-mingw, CMake and Ninja into .deps/
.deps/cmake/bin/cmake --preset windows-release
.deps/cmake/bin/cmake --build build/windows-release
```

### Build (Linux)

```sh
cmake --preset linux-release                 # clang 18+, CMake, Ninja; OpenSSL, Wayland and libsecret headers
cmake --build build/linux-release
```

## License

RiftLift is licensed under GPL-v3: see [LICENSE](LICENSE).

It's built on other people's work, and we're grateful for it. Code from these projects is used under their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md):

| License | Whose code |
|---|---|
| [MIT](runtime/LICENSE) | [Revive](https://github.com/LibreVR/Revive), by LibreVR and its contributors: the Oculus API bridges |
| GPL-3.0 | [xrizer](https://github.com/Villagers654/xrizer) (RiftLift's fork): OpenVR on OpenXR, on Linux |
| Upstream licenses | [GE-Proton](https://github.com/GloriousEggroll/proton-ge-custom), by GloriousEggroll, downloaded when you first play on Linux |
| [ISC](THIRD_PARTY_NOTICES.md#lucide) | [Lucide](https://lucide.dev) icons |

Thank you to LibreVR and everyone who has worked on Revive, which made Rift games on other headsets possible; to the xrizer, Wine, Proton and DXVK projects that RiftLift runs games on; and to GloriousEggroll for GE-Proton.

RiftLift is not affiliated with Meta, Oculus or Valve. It only downloads games your Meta account owns, and it is compatibility software, not a DRM bypass.

## Star history

<a href="https://www.star-history.com/?type=date&repos=Villagers654%2FRiftLift">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=Villagers654/RiftLift&type=date&theme=dark&legend=top-left&sealed_token=vpFI0AQgUej_RbdUU8YiyenZTK4Yztdp64p7xfU8enm1_6nreoY8RC6_R6pb9Xt5IprDK8Wnsy-OOpIULPGKabYF5lu3DeJI8RPvEseMStENO9BmhSLT4JrCaiFTUAhlkr6m3kJyat-sHGo_oFTht_YW1VJq04oQBvI-rUdAWrkQURYzvz2MEhpzDOL0" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=Villagers654/RiftLift&type=date&legend=top-left&sealed_token=vpFI0AQgUej_RbdUU8YiyenZTK4Yztdp64p7xfU8enm1_6nreoY8RC6_R6pb9Xt5IprDK8Wnsy-OOpIULPGKabYF5lu3DeJI8RPvEseMStENO9BmhSLT4JrCaiFTUAhlkr6m3kJyat-sHGo_oFTht_YW1VJq04oQBvI-rUdAWrkQURYzvz2MEhpzDOL0" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=Villagers654/RiftLift&type=date&legend=top-left&sealed_token=vpFI0AQgUej_RbdUU8YiyenZTK4Yztdp64p7xfU8enm1_6nreoY8RC6_R6pb9Xt5IprDK8Wnsy-OOpIULPGKabYF5lu3DeJI8RPvEseMStENO9BmhSLT4JrCaiFTUAhlkr6m3kJyat-sHGo_oFTht_YW1VJq04oQBvI-rUdAWrkQURYzvz2MEhpzDOL0" />
 </picture>
</a>
