#pragma once
// The user's XR runtime selection and the platform identity games see.
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
// A stable user ID for the platform shim: RIFTLIFT_USER_ID, else
// <config>/platform-user-id, created once with a random 62-bit value.
std::string platform_user_id(const Layout &layout, const Env &env);

} // namespace riftlift::runtime
