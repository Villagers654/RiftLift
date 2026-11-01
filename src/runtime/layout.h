#pragma once
// Where the runtime keeps its state: the same locations the Python RiftLift
// used, so existing prefixes, payloads and caches keep working.
#include "common/paths.h"
#include <string>

namespace riftlift::runtime {

inline constexpr std::string_view payload_version = RIFTLIFT_PAYLOAD_VERSION;
// "riftlift-<version>", recorded in installed payloads' .riftlift-version.
std::string runtime_version();
// https://github.com/Villagers654/RiftLift/releases/download/v<version>
std::string release_url();

struct Layout {
  fs::path data;    // game records, diagnostics
  fs::path cache;   // downloaded archives
  fs::path config;  // debug-logging, OpenVR registry
  fs::path prefix;  // Proton compat data (the Wine prefix is prefix/pfx)
  fs::path tools;   // installed payloads
};
// Linux: data is `root` itself, cache and config follow XDG; Windows: all
// below `root` (%LOCALAPPDATA%\RiftLift).
Layout layout(const fs::path &root);

// $XDG_CONFIG_HOME, falling back to ~/.config (an unset or relative value is
// ignored, as the XDG specification requires).
fs::path xdg_config_home();
fs::path home_dir();
// The debug-logging switch RiftLift's settings write.
bool debug_logging_enabled(const Layout &layout);

} // namespace riftlift::runtime
