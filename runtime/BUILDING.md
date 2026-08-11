# Building the RiftLift runtime

The runtime has two deliberate halves inside each game process:

- `windows-openxr/`, `windows-openvr/`, and `windows-launcher/` expose the
  Windows Oculus ABI and handle Windows graphics objects.
- GE-Proton's paired PE/ELF Wine modules carry calls across Wine's supported
  `unixlib` boundary. `wineopenxr.dll` pairs with `wineopenxr.so` for OpenXR;
  `vrclient_x64.dll` pairs with `vrclient_x64.so` for OpenVR. The ELF side
  talks to the selected Linux runtime directly.

There is no helper daemon, socket protocol, or separately installed runtime.
The boundary is in-process, which is required for graphics handles and avoids
creating a second XR instance beside the game.

## Windows ABI bridge

RiftLift's CI builds `RiftLiftRuntime.sln` with the pinned Oculus SDK, OpenXR,
OpenVR, and Detours dependencies. The release workflow packages those PE ABI
files; the pinned GE-Proton distribution supplies the matching Wine unixlibs,
and RiftLift validates both binary halves before every launch. See the
top-level workflows for the reproducible dependency and packaging commands.

## Design rules

- Linux XR calls cross Wine's in-process unixlib boundary; do not add a helper
  process or network transport.
- PE code is limited to the Oculus ABI and Windows/Wine graphics objects.
- Compatibility decisions are capability-based; never add title allowlists.
- Preserve the upstream license and credit in `LICENSE` and `README.md`.
