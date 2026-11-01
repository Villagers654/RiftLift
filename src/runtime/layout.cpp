#include "runtime/layout.h"
#include "common/environment.h"
#include <stdexcept>

namespace riftlift::runtime {
std::string runtime_version() { return "riftlift-" + std::string(payload_version); }
std::string release_url() {
  return "https://github.com/Villagers654/RiftLift/releases/download/v" +
         std::string(payload_version);
}
fs::path home_dir() {
#ifdef _WIN32
  if (auto p = environment::getenv("USERPROFILE"); p && *p)
    return paths::from_utf8(p);
#else
  if (auto p = environment::getenv("HOME"); p && *p)
    return paths::from_utf8(p);
#endif
  throw std::runtime_error("The home folder is unknown; set HOME.");
}
namespace {
fs::path xdg(const char *name, std::string_view fallback) {
  if (auto p = environment::getenv(name); p && *p) {
    auto path = paths::from_utf8(p);
    if (path.is_absolute())
      return path;
  }
  return home_dir() / fs::path(fallback);
}
} // namespace
fs::path xdg_config_home() { return xdg("XDG_CONFIG_HOME", ".config"); }
Layout layout(const fs::path &root) {
#ifdef _WIN32
  return {root / "data", root / "cache", root / "config", root / "compatdata", root / "tools"};
#else
  return {root, xdg("XDG_CACHE_HOME", ".cache") / "riftlift", xdg_config_home() / "riftlift",
          root / "compatdata", root / "tools"};
#endif
}
bool debug_logging_enabled(const Layout &layout) {
  std::error_code ec;
  return fs::is_regular_file(layout.config / "debug-logging", ec);
}
} // namespace riftlift::runtime
