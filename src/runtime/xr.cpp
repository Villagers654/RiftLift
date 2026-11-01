#include "runtime/xr.h"
#include "runtime/files.h"
#include "runtime/rules.h"
#include <stdexcept>

namespace riftlift::runtime {
fs::path active_runtime_json(const Env &env) {
  if (auto explicit_value = strip(get(env, "XR_RUNTIME_JSON")); !explicit_value.empty()) {
    auto target = paths::from_utf8(explicit_value);
    std::error_code ec;
    if (!target.is_absolute() || !fs::is_regular_file(target, ec))
      throw std::runtime_error("XR_RUNTIME_JSON does not name an existing absolute file: " +
                               explicit_value);
    return fs::canonical(target);
  }
  auto candidate = xdg_config_home() / "openxr/1/active_runtime.json";
  std::error_code ec;
  if (fs::is_regular_file(candidate, ec))
    return fs::canonical(candidate);
  throw std::runtime_error("no active OpenXR runtime is configured at " + paths::utf8(candidate) +
                           "; select one with your runtime manager or set XR_RUNTIME_JSON to its "
                           "absolute manifest path");
}
std::optional<fs::path> steamvr_runtime_for_openxr(const fs::path &manifest) {
  try {
    auto payload = parse_json(read_file(manifest));
    const auto &description = payload.at("runtime");
    if (!description.is_object())
      return std::nullopt;
    const bool steamvr = field(description, "VALVE_runtime_is_steamvr") == true ||
                         lower(str(description, "name")) == "steamvr";
    if (!steamvr)
      return std::nullopt;
    auto root = fs::canonical(manifest).parent_path();
    std::error_code ec;
    if (!fs::is_regular_file(root / "bin/linux64/vrclient.so", ec))
      return std::nullopt;
    return root;
  } catch (...) {
    return std::nullopt;
  }
}
} // namespace riftlift::runtime
