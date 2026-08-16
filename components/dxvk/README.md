# RiftLift DXVK compatibility build

RiftLift builds the D3D11 and DXGI DLLs from DXVK 3.0.2 commit
`3a4c6fa3cb1548d56a90a38dd8f526b6c13e63fd` and applies
`monotonic-fence-signals.patch`.

Direct3D fences permit applications to signal their current or an older value,
but Vulkan timeline semaphore signals must strictly increase. The patch treats
non-increasing D3D signals as no-ops instead of submitting invalid Vulkan work.
RiftLift setup installs the resulting 32-bit and 64-bit DLLs into its pinned,
dedicated GE-Proton build.
