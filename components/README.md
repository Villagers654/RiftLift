# Compatibility components

RiftLift maintains its translation layers together while keeping their source
histories distinct:

- `xrizer/` is a pinned submodule of the maintained
  [RiftLift xrizer fork](https://github.com/Villagers654/xrizer), derived from
  [Supreeeme/xrizer](https://github.com/Supreeeme/xrizer).
- `dxvk/` pins the upstream DXVK source revision and the small generic fence
  compatibility patch used to build RiftLift's D3D11/DXGI payload.

The Rift compatibility runtime is a first-class part of RiftLift and lives at
the repository root in [`runtime/`](../runtime).

RiftLift-specific xrizer fixes are made in the fork and tested there, then this
repository's submodule pointer is updated. The top-level CI workflow tests the
exact pinned revision again as part of RiftLift.

The release workflow builds both components and assembles the downloadable
RiftLift compatibility payload from those exact sources.
