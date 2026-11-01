#pragma once
// Linux: installing GE-Proton, RiftLift's DXVK, Meta's runtime, the release
// payloads and XRizer into RiftLift's private prefix, and the environment a
// game runs with (runtime.py).
#include "runtime/child.h"
#include "runtime/layout.h"
#include <optional>
#include <stop_token>

namespace riftlift::runtime {

inline constexpr std::string_view proton_version = "GE-Proton11-3";
inline constexpr std::string_view dxvk_version = "3.0.2-riftlift.1";

struct OpenVrSelection {
  fs::path runtime, registry;
  std::string kind;  // steamvr, xrizer or external
};

class Installer {
public:
  Installer(Layout layout, Env base, std::stop_token stop);
  const Layout &layout() const { return layout_; }
  const Env &base() const { return base_; }
  fs::path steam_root() const;
  // GE-Proton with RiftLift's DXVK; installed on first use.
  fs::path install_proton();
  fs::path install_rift_runtime();
  fs::path install_openvr_runtime();
  fs::path install_meta_runtime();
  fs::path install_platform_compat();
  void install_openxr_layer();
  // Installs every component above.
  void setup();
  // --check: each component as installed now, against what this build
  // installs; reads only, never downloads or changes anything. Lines are
  // "OK  label: detail", "FAIL label: detail" or "--  label: detail" (not
  // installed yet, or not in use).
  std::vector<std::string> check();
  // Checks GE-Proton's Wine unixlib pair for `backend`; returns "pe -> unix".
  std::string native_xr_bridge(std::string_view backend);
  OpenVrSelection select_openvr_runtime(const std::optional<fs::path> &openxr_runtime = {});
  Env proton_environment(const std::optional<fs::path> &game_dir = {});
  Env launch_environment(const fs::path &game_dir, bool platform_shim, bool platform_offline,
                         const fs::path &runtime_json);
  // Removes Proton's cached OpenVR runtime so this launch's selection is used.
  void clear_proton_openvr_cache();
  // Stops the prefix's wineserver and everything it runs.
  void shutdown_compat_prefix(bool wait = true);
  // Whether this run initialized the prefix or changed its registry.
  bool prefix_changed() const { return prefix_changed_; }
  bool debug_logging() const;

private:
  void install_dxvk_compat(const fs::path &proton);
  fs::path initialize_prefix();
  void proton(const std::vector<std::string> &args);
  void registry_add(const std::string &key, const std::optional<std::string> &name,
                    const std::string &kind, const std::string &value);
  void install_meta_signing_root(const fs::path &support);
  void configure_meta_registry(const fs::path &support);
  void validate_openvr_library(const fs::path &library);
  fs::path write_openvr_path_registry(const fs::path &runtime);
  fs::path private_openvr_path_registry(const fs::path &source);
  fs::path archive_override(const char *variable) const;

  Layout layout_;
  Env base_;
  std::stop_token stop_;
  std::optional<fs::path> proton_;
  bool prefix_changed_ = false;
};

} // namespace riftlift::runtime
