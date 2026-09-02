# Experimental native Windows host

This branch adds a native Windows entry point to RiftLift. It is **not yet a
verified Windows game-compatibility release**. The existing Linux host remains
the default on Linux. The native host reuses the upstream x64 PE launcher and
OpenXR/OpenVR backends from v0.10.2.2 without Wine, Proton, DXVK, or xrizer.

## Install and launch

Requirements: x64 Windows, Python 3.12, Git, a working native Windows OpenXR or
OpenVR headset runtime, and installed PC game files.

```powershell
.\install-windows.ps1
.\RiftLift.cmd
.\RiftLift.cmd doctor --no-paste
.\RiftLift.cmd add-local "C:\Games\Example\Game.exe" --name "Example"
.\RiftLift.cmd launch example --backend openxr --dry-run
.\RiftLift.cmd launch example --backend openxr
```

For SteamVR use `--backend openvr`. The selected runtime must already be installed
and configured; this installer does not change the system's active XR runtime.
Run games through their normal client once first to finish prerequisites and
account setup. The GUI lets you add installed executables, select a backend, view
local diagnostics, and launch a game. It keeps launch work off the UI thread.

## Current boundaries

- Native Windows sign-in, store downloading, Steam shortcut management, and
  playtime tracking are not implemented in this experimental frontend.
- Install purchases with Meta's PC application before adding their executables.
  Original platform authentication and entitlement checks remain in place.
- Game folders are referenced in place. No game executable or platform DLL is
  replaced. The Linux compatibility platform shim is deliberately not installed.
- Only x64 executables are accepted. OpenXR registration and file checks are
  preflight checks, not proof of a working headset session.
- `doctor` never uploads a paste. Exit 2 means the native payload/runtime
  preconditions are incomplete; exit 0 still does not certify gameplay.
- Application state is under `portable/` with `RiftLift.cmd`; direct CLI use
  defaults to `%LOCALAPPDATA%\RiftLift`. Set `RIFTLIFT_HOME` to override.
- Launch logs are under `portable/data/logs/`; the native launcher also writes
  `%LOCALAPPDATA%\RiftLift\RiftLiftLauncher.txt`.

## Game test matrix

| Game | Windows result on initial host |
| --- | --- |
| Lone Echo | Pending installed PC game and configured headset |
| Stormland | Pending installed PC game and configured headset |
| Vader Immortal | Pending installed PC game, episode selection, and headset |
| Oculus First Contact | Pending installed PC game and configured headset |
| Echo VR | Pending community installation/account linking and headset |

Echo VR community installation is separate from RiftLift. Start with the
[Echo VR Lounge community](https://discord.com/servers/echo-vr-lounge-779349159852769310)
and its current PC patching instructions; multiplayer account linking requires
the player's participation. Do not substitute a Quest APK for the Windows game.

For each title, verify launch, both-eye rendering, tracking, controller mapping,
audio, menu interaction, and gameplay. Record native backend, headset connection,
game build, exit code, and logs. Injection success alone is not a game pass.

## Tests and rollback

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest tests/test_windows_native.py tests/test_util.py tests/test_library.py -q
```

The Linux-focused full suite includes POSIX paths, permissions, and `fcntl` and
is not a Windows acceptance suite. Existing Linux CI continues to cover that
host; the added Windows job covers the native frontend and shared utilities.

Close games and RiftLift before undoing the installation. The installation is
local to this checkout (`.venv/` and `portable/`); it does not register services,
change XR registry keys, replace game files, or alter another VR installation.
Keep `portable/` if you want to preserve the local library. Revert the branch's
commits to restore upstream source behavior.
