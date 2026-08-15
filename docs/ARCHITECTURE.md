# Architecture and ownership

RiftLift is the complete application-level compatibility layer for owned Meta
Rift PC games on Linux. Given a working standards-based OpenXR runtime, RiftLift
owns:

- the isolated GE-Proton prefix and Meta Horizon Link runtime packages;
- native browser-backed Meta login and entitlement-backed downloads;
- the maintained RiftLift Oculus ABI runtime and native Wine unixlib paths;
- legacy Platform SDK forwarding and compatibility shims;
- game manifests, metadata, Steam shortcuts, and launch lifecycle; and
- runtime discovery, diagnostics, updates, and optional generic launch hooks.

RiftLift deliberately does not own headset drivers, USB permissions, display
routing, Monado builds, compositor startup, boundaries, or controller firmware.
Those belong to the user's working OpenXR/headset setup. RiftLift consumes the
standard active OpenXR manifest and never assumes PSVR2, WayVR, Envision, or a
particular Monado service name.

RiftLift can create those manifests from entitlement-backed Meta downloads,
installed Steam Oculus builds, or local Windows game folders. Local entries
remain in place and are never treated as ownership-verified Meta downloads.

## Rendering paths

Oculus-only titles use the shortest path:

```text
Rift game -> RiftLift Oculus ABI -> wineopenxr.dll/so -> Linux OpenXR runtime
```

Titles that bundle both Oculus and OpenVR integrations use an OpenVR compositor
path selected from the active headset runtime:

```text
SteamVR: Game -> RiftLift Oculus ABI -> Valve vrclient_x64.dll/so -> SteamVR
Monado:  Game -> RiftLift Oculus ABI -> vrclient_x64.dll/so -> xrizer -> OpenXR
```

The paired DLL/ELF modules are Wine's supported in-process `unixlib` boundary.
The DLL side receives Windows ABI and graphics objects; the ELF side calls the
Linux runtime directly. RiftLift does not create a second XR process or proxy
runtime calls over a socket.

The choice is made from installed runtime capabilities, not a game-name list or
a failed-launch retry. A running SteamVR session selects Valve's native OpenVR
client directly; XRizer, Vapor, and OpenComposite are not loaded on that path.
For Monado and other OpenXR-only runtimes, RiftLift installs and pins its
in-repository xrizer build. An explicit `VR_OVERRIDE` remains available for
runtime development and debugging.

## Host integration contract

A host integration only needs to expose a valid OpenXR manifest through the
standard loader selection or `XR_RUNTIME_JSON`. If it must start a compositor
or acquire a display before launching, it may set `RIFTLIFT_LAUNCH_WRAPPER` to a
generic command that accepts the normal RiftLift launch command as trailing
arguments. No device-specific executable is discovered implicitly.
