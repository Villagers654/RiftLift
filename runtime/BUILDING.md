# Building the RiftLift runtime

The runtime has two deliberate halves:

- `native/` is a Linux process linked directly to the system OpenXR loader. In
  OpenVR mode it loads the native `vrclient.so` selected by `VR_OVERRIDE`.
- `windows-openxr/`, `windows-openvr/`, and `windows-launcher/` are the thin PE
  ABI and graphics bridge required because Rift games are Windows binaries.

The Windows bridge authenticates to the native host at Oculus initialization.
It is not a separately installed or branded application.

## Linux host

Install a C++20 compiler, CMake, Ninja, and the OpenXR development package,
then run from the repository root:

```bash
cmake -S runtime -B runtime/build-native -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build runtime/build-native
```

The result is `runtime/build-native/riftlift-runtime-host`. It can verify either
native API path against a running headset stack:

```bash
runtime/build-native/riftlift-runtime-host --backend=openxr
VR_OVERRIDE=/path/to/openvr-runtime \
  runtime/build-native/riftlift-runtime-host --backend=openvr
```

## Windows ABI bridge

RiftLift's CI builds `RiftLiftRuntime.sln` with the pinned Oculus SDK, OpenXR,
OpenVR, and Detours dependencies. The release workflow combines those PE files
with the Linux host in one compatibility payload. See the top-level workflows
for the reproducible dependency and packaging commands.

## Design rules

- Native OpenXR/OpenVR lifecycle belongs in the Linux host.
- PE code is limited to the Oculus ABI, Wine graphics objects, and transport.
- Compatibility decisions are capability-based; never add title allowlists.
- Preserve the upstream license and credit in `LICENSE` and `README.md`.
