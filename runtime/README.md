# RiftLift Rift runtime

This directory contains RiftLift's Oculus PC SDK compatibility runtime. It is
built, versioned, tested, and shipped as part of RiftLift rather than installed
as a separate application.

The Windows-facing portion implements the Oculus ABI expected by Rift games.
The Linux port is moving runtime ownership across Wine's supported PE-to-Unix
boundary so OpenXR lifecycle, tracking, and input can run in a native host
component. See [BUILDING.md](BUILDING.md) for contributor instructions.

RiftLift selects compatibility behavior from executable capabilities. Runtime
code must not contain title allowlists or per-game exceptions.

## Upstream credit

This runtime is derived from [LibreVR/Revive](https://github.com/LibreVR/Revive)
and remains licensed under GPL-3.0. The original copyright and license notice is
preserved in [LICENSE](LICENSE). RiftLift is grateful to LibreVR and all Revive
contributors for the Oculus-to-OpenVR/OpenXR implementation this work builds
upon.
