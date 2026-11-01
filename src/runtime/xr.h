#pragma once
// The user's XR runtime selection.
#include "runtime/child.h"
#include "runtime/layout.h"
#include <optional>

namespace riftlift::runtime {

// XR_RUNTIME_JSON when set (an existing absolute file), else the active
// runtime registered in $XDG_CONFIG_HOME/openxr/1; throws when neither is.
fs::path active_runtime_json(const Env &env);
// SteamVR's root when `manifest` is Valve's OpenXR manifest beside a usable
// OpenVR client (bin/linux64/vrclient.so).
std::optional<fs::path> steamvr_runtime_for_openxr(const fs::path &manifest);

} // namespace riftlift::runtime
