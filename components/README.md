# Compatibility components

RiftLift maintains its translation layers in this repository:

- `xrizer/` contains the native OpenVR-to-OpenXR runtime derived from
  [Supreeeme/xrizer](https://github.com/Supreeeme/xrizer).

The Rift compatibility runtime is a first-class part of RiftLift and lives at
the repository root in [`runtime/`](../runtime).

These are source components, not Git submodules. RiftLift-specific fixes should
be made here and tested through the top-level CI workflow. The only submodules
are unmodified upstream build dependencies used by the Rift runtime.

The release workflow builds both components and assembles the downloadable
RiftLift compatibility payload from those exact sources.
