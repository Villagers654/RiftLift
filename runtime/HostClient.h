#pragma once

// Establishes the versioned control connection to RiftLift's native Linux
// runtime. The host owns OpenXR/OpenVR; this process only exposes the Windows
// Oculus ABI and graphics objects required by the game.
bool RiftLiftConnectNativeHost();
