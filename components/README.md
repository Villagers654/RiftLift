# Compatibility components

RiftLift maintains its two translation layers in this repository:

- `revive/` contains the Windows Revive and ReviveXR backends derived from
  [LibreVR/Revive](https://github.com/LibreVR/Revive).
- `xrizer/` contains the native OpenVR-to-OpenXR runtime derived from
  [Supreeeme/xrizer](https://github.com/Supreeeme/xrizer).

These are source components, not Git submodules. RiftLift-specific fixes should
be made here and tested through the top-level CI workflow. The only submodules
are unmodified upstream build dependencies used by Revive.

The release workflow builds both components and assembles the downloadable
RiftLift compatibility payload from those exact sources.
