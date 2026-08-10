# Architecture and ownership

RiftLift is the complete application-level compatibility layer for owned Meta
Rift PC games on Linux. Given a working standards-based OpenXR runtime, RiftLift
owns:

- the isolated GE-Proton prefix and Meta Horizon Link runtime packages;
- native browser-backed Meta login and entitlement-backed downloads;
- the maintained ReviveXR compatibility payload and WineOpenXR launch path;
- legacy Platform SDK forwarding and compatibility shims;
- game manifests, metadata, Steam shortcuts, and launch lifecycle; and
- runtime discovery, diagnostics, updates, and optional generic launch hooks.

RiftLift deliberately does not own headset drivers, USB permissions, display
routing, Monado builds, compositor startup, boundaries, or controller firmware.
Those belong to the user's working OpenXR/headset setup. RiftLift consumes the
standard active OpenXR manifest and never assumes PSVR2, WayVR, Envision, or a
particular Monado service name.

## Rendering paths

Rift titles use the shortest path:

```text
Rift game -> ReviveXR -> WineOpenXR -> active Linux OpenXR runtime
```

[RiftLift xrizer](https://github.com/Villagers654/xrizer) remains the maintained
OpenVR-to-OpenXR project for applications that actually use OpenVR. It is not
injected into the Rift path above, and RiftLift does not replace an xrizer
already supplied by the host setup.

## Host integration contract

A host integration only needs to expose a valid OpenXR manifest through the
standard loader selection or `XR_RUNTIME_JSON`. If it must start a compositor
or acquire a display before launching, it may set `RIFTLIFT_LAUNCH_WRAPPER` to a
generic command that accepts the normal RiftLift launch command as trailing
arguments. No device-specific executable is discovered implicitly.
