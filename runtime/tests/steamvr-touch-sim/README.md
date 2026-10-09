# Simulated Touch controllers for SteamVR (test only)

A minimal SteamVR driver that adds a left and right Oculus Touch controller,
for exercising RiftLift without hardware. It provides no headset; pair it with
SteamVR's null HMD (`"forcedDriver": "null"`) or any real headset. Nothing in
RiftLift knows about it: games see ordinary Touch controllers through whatever
runtime is active (SteamVR's OpenVR interface or its OpenXR runtime).

The controllers report SteamVR's own Touch input profile
(`{oculus}/input/touch_profile.json`), rest in front of the headset, and follow
it.

## Build

```bash
cmake -S runtime/tests/steamvr-touch-sim -B build/touchsim
cmake --build build/touchsim --config Release
```

The output folder `build/touchsim/riftlift_touchsim` is a complete SteamVR
driver. Any C++17 compiler with the pinned OpenVR headers works (MSVC or
llvm-mingw).

## Use

```bash
python scripts/steamvr-touch-sim.py register build/touchsim/riftlift_touchsim
# restart SteamVR, then drive inputs while a game runs:
python scripts/steamvr-touch-sim.py press right trigger --hold 3
python scripts/steamvr-touch-sim.py set left stickx -1
python scripts/steamvr-touch-sim.py unregister build/touchsim/riftlift_touchsim
```

Inputs: `a b x y system trigger grip stickx sticky stickclick thumbrest`.
Commands go through `%LOCALAPPDATA%\RiftLift\touchsim.txt`, which the driver
reads and deletes.
