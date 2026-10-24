# RiftLift

**Play your Meta Rift (Oculus Rift) PC VR games on Windows and Linux.**

RiftLift signs in to your Meta account, downloads the Rift / PC VR games you
own, and launches them through the VR setup you already use (SteamVR or an
OpenXR runtime such as Monado).

![RiftLift library](docs/images/riftlift-library.png)

> [!WARNING]
> RiftLift is alpha software. See the [compatibility wiki](docs/COMPATIBILITY.md)
> for tested games.

## Requirements

- A 64-bit Windows or Linux PC
- A VR headset that already works with SteamVR or another OpenXR runtime
- A Meta account that owns a **Rift / PC VR** game (Quest-only purchases are not
  PC games; cross-buy titles work)

RiftLift doesn't install headset drivers or VR runtimes.

## Install

**Windows:** download `RiftLift-Setup-<version>-x64.exe` from the
[latest release](https://github.com/Villagers654/RiftLift/releases/latest) and
run it. No administrator rights are needed. The installer isn't code-signed yet,
so if SmartScreen appears, choose **More info → Run anyway**. To update, run a
newer installer; to uninstall, use **Settings → Apps → Installed apps**. Your
games and settings are kept.

**Linux:** download `riftlift-installer.sh` from the
[latest release](https://github.com/Villagers654/RiftLift/releases/latest) and run:

```bash
bash riftlift-installer.sh
```

It verifies every download, installs the compatibility stack, and adds RiftLift
to your app menu. Run a newer installer to update.

## Getting started

1. **Sign in.** Click **Sign In** and finish Meta's sign-in page in your browser.
   Your password and security codes go only to Meta.
   To add games from another Meta account, open **Account** and click
   **Add another account**; games owned by every signed-in account appear
   together in your library.

   ![Sign in to Meta](docs/images/riftlift-account.png)

2. **Add a game.** Your owned games appear under **Not installed**. Select one
   and click **Install**, or click **Add Game** and paste a game's Meta
   **Rift / PC VR** store link. Delisted games you still own, such as Echo VR,
   install the same way.

   The **Version** list in **Add Game** shows every build your account can
   download, including older and beta-channel releases. The first entry
   installs the newest one and updates an existing install in place; an older
   one installs beside it as its own library entry. **All versions** downloads
   every build into its own folder, which can take a lot of disk space.

   ![Add Game](docs/images/riftlift-add-game.png)

3. **Play.** Select the game and click **Launch**. RiftLift picks your VR
   runtime automatically and tracks playtime locally.

### More

- **Games installed elsewhere:** in **Add Game**, choose **Add a local game…**
  and pick the game's `.exe`. RiftLift uses it in place.

  ![Add a local game](docs/images/riftlift-local-game.png)

- **Steam games with an Oculus mode** (Linux): click **Steam Games** to find
  installed Steam titles RiftLift can launch in Oculus mode. Steam still owns and
  updates them. On Linux, RiftLift can also add Steam shortcuts for your games.

  ![Steam games with an Oculus mode](docs/images/riftlift-steam-games.png)

- **Command line** (Linux): `riftlift login`, `riftlift add <store-url>`,
  `riftlift list`, `riftlift launch <game>` and `riftlift doctor` mirror the app.
  Run `riftlift login` again to add another account; `riftlift accounts` and
  `riftlift logout [account]` manage them. `riftlift builds <store-url>` lists
  every version you can download; `riftlift add <store-url> --build <version>`
  installs one beside your current install, and `--build all` installs every
  one. Run `riftlift --help` for everything else.

## Troubleshooting

Open **Settings** to check whether everything is ready. If a game won't start:

1. Turn on **Settings → Debug logging** and try the game once more.
2. Click **Generate a diagnostic report** (or run `riftlift doctor`) and attach it
   to a [new issue](https://github.com/Villagers654/RiftLift/issues).

Reports are redacted: no credentials, email addresses or home paths.

Common fixes:

- **No VR runtime found:** start SteamVR (or your OpenXR runtime) and try again.
- **Meta asks you to sign in again:** click **Sign In** and finish Meta's page.
- **A game is missing from Steam (Linux):** close Steam, run `riftlift steam-sync`,
  and reopen it.

## Contributing

RiftLift is a Python/Qt desktop app plus a native runtime that translates the
Oculus API for each game.

| Path | What it is |
| --- | --- |
| `src/riftlift/` | The app: UI (PySide6), Meta sign-in and downloads, library, launching |
| `runtime/` | C++ Oculus → OpenVR/OpenXR bridges and the Windows launcher, derived from [Revive](https://github.com/LibreVR/Revive) |
| `runtime/openxr-layer/` | OpenXR layer that lets OVRPlugin (Unity/Unreal) games use non-Oculus runtimes |
| `compat/` | Oculus Platform SDK compatibility shim |
| `components/xrizer/` | Submodule: RiftLift's [xrizer fork](https://github.com/Villagers654/xrizer) (OpenVR → OpenXR, Linux) |
| `scripts/`, `.github/workflows/` | Packaging and CI (Windows installer, AppImage, Linux installer, release) |

On Windows, games run natively through the bridges. On Linux they run under
GE-Proton, and the bridges reach the host's OpenXR runtime or SteamVR through
Wine's in-process `unixlib` boundary.

**Set up a dev environment**

- Linux: `./install.sh` installs from your checkout.
- Windows: with Python 3.12 and Git, run `./install-windows.ps1`, then start
  `RiftLift.cmd`.

**Before opening a PR**

```bash
python -m pytest
ruff check src tests && ruff format --check src tests
```

CI builds the native runtime and the Windows installer, and runs the Linux and
Windows tests. To test without hardware,
[`runtime/tests/steamvr-touch-sim`](runtime/tests/steamvr-touch-sim/README.md)
adds simulated Touch controllers to SteamVR's null headset.

## Credits and legal

RiftLift's Oculus API translation is derived from the MIT-licensed
[Revive](https://github.com/LibreVR/Revive) project; thanks to LibreVR and its
contributors.

RiftLift is unaffiliated with Meta, Oculus or Valve. It only downloads games your
Meta account owns, after checking your entitlement. It is compatibility software,
not a DRM bypass.

RiftLift is MIT-licensed. Bundled components keep their own licenses; see
[third-party notices](THIRD_PARTY_NOTICES.md).

## Star history

<a href="https://www.star-history.com/?type=date&repos=Villagers654%2FRiftLift">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=Villagers654/RiftLift&type=date&theme=dark&legend=top-left&sealed_token=vpFI0AQgUej_RbdUU8YiyenZTK4Yztdp64p7xfU8enm1_6nreoY8RC6_R6pb9Xt5IprDK8Wnsy-OOpIULPGKabYF5lu3DeJI8RPvEseMStENO9BmhSLT4JrCaiFTUAhlkr6m3kJyat-sHGo_oFTht_YW1VJq04oQBvI-rUdAWrkQURYzvz2MEhpzDOL0" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=Villagers654/RiftLift&type=date&legend=top-left&sealed_token=vpFI0AQgUej_RbdUU8YiyenZTK4Yztdp64p7xfU8enm1_6nreoY8RC6_R6pb9Xt5IprDK8Wnsy-OOpIULPGKabYF5lu3DeJI8RPvEseMStENO9BmhSLT4JrCaiFTUAhlkr6m3kJyat-sHGo_oFTht_YW1VJq04oQBvI-rUdAWrkQURYzvz2MEhpzDOL0" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=Villagers654/RiftLift&type=date&legend=top-left&sealed_token=vpFI0AQgUej_RbdUU8YiyenZTK4Yztdp64p7xfU8enm1_6nreoY8RC6_R6pb9Xt5IprDK8Wnsy-OOpIULPGKabYF5lu3DeJI8RPvEseMStENO9BmhSLT4JrCaiFTUAhlkr6m3kJyat-sHGo_oFTht_YW1VJq04oQBvI-rUdAWrkQURYzvz2MEhpzDOL0" />
 </picture>
</a>
