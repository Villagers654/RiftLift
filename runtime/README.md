# RiftLift Rift runtime

This directory contains RiftLift's Oculus PC SDK compatibility runtime. It is
built, versioned, tested, and shipped as part of RiftLift rather than installed
as a separate application.

The Windows-facing portion implements the Oculus ABI expected by Rift games.
XR calls cross Wine's supported in-process PE-to-Unix boundary into GE-Proton's
native `wineopenxr.so` or `vrclient_x64.so`, which then talks directly to the
selected Linux OpenXR/OpenVR runtime. See [BUILDING.md](BUILDING.md) for
contributor instructions.

RiftLift selects compatibility behavior from executable capabilities. Runtime
code must not contain title allowlists or per-game exceptions.

## Upstream credit

This runtime is derived from [LibreVR/Revive](https://github.com/LibreVR/Revive)
and remains licensed under GPL-3.0. The original copyright and license notice is
preserved in [LICENSE](LICENSE). RiftLift is grateful to LibreVR and all Revive
contributors for the Oculus-to-OpenVR/OpenXR implementation this work builds
upon.
