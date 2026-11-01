#pragma once
// The launch decisions that depend only on their inputs: Wine paths, DLL
// override merging, the XR backend, engine arguments and environment rules.
// Kept apart from the installers so tests can check them directly.
#include "common/paths.h"
#include "runtime/child.h"
#include <functional>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <vector>

namespace riftlift::runtime {

std::string lower(std::string_view text);
std::string strip(std::string_view text);

// "Z:\home\user\game" for an absolute POSIX path, unchanged otherwise.
std::string wine_path(const fs::path &absolute);
// wine_path of the path with symlinks resolved (Python's linux_to_windows).
std::string linux_to_windows(const fs::path &path);

// mods.py: per-game rules replace inherited rules for the same DLLs ("*"
// replaces all of them); throws std::invalid_argument for malformed rules.
void apply_dll_overrides(Env &env, std::string_view overrides);
// MelonLoader/BepInEx proxy DLLs beside the game executable load native
// first unless an explicit rule already names them.
void configure_mod_loaders(Env &env, const fs::path &executable);

// openxr or openvr: `override_value` (RIFTLIFT_RUNTIME_BACKEND) when set,
// else OpenVR for installs needing it (OpenVR, Unity's Oculus plugin, D3D12,
// legacy OVR presentation arguments).
std::string select_backend(std::string_view override_value,
                           const std::vector<std::string> &arguments,
                           const std::set<std::string> &capabilities);

enum class Engine { other, unreal, unity };
// The game's arguments with the engine's Oculus mode selected.
std::vector<std::string> oculus_launch_arguments(Engine engine, std::vector<std::string> arguments);

// RiftLiftLauncher.exe's command line (after `proton run`).
std::vector<std::string> launcher_arguments(const std::string &launcher, std::string_view backend,
                                            const std::string &app_key, const std::string &cwd,
                                            const std::string &executable,
                                            const std::vector<std::string> &arguments);

// A standalone OpenVR runtime must not share the compositor with wineopenxr;
// XRizer is itself an OpenXR client and keeps it.
void disable_openxr_for_direct_openvr(Env &env, std::string_view openvr_kind);
// GE-Proton/UMU identity: the Steam app for Steam games, else a default.
void configure_proton_identity(Env &env, std::string_view source, std::uint64_t steam_app_id);

// (app directory, app id) of a runtime inside a Flatpak app's files.
std::optional<std::pair<fs::path, std::string>> flatpak_app_parts(const fs::path &runtime);
// WiVRn's "set command to KEY=value ... %command%" advice, accepted only
// when every assignment is a PRESSURE_VESSEL_* flag set to 0/1 or to
// existing absolute folders.
std::optional<std::map<std::string, std::string>> parse_pressure_vessel_line(
    std::string_view line, const std::function<bool(const fs::path &)> &is_directory);

// Whether system.reg already has `name`=dword:0 under the HKLM `key`.
bool layer_registered(std::string_view system_reg, std::string_view key, std::string_view name);

// Proton's environment for RiftLift's private prefix (proton_environment).
struct ProtonSettings {
  fs::path prefix, steam_root, install_path;
  bool debug = false;
  // Debug log folders; used only when `debug`.
  fs::path proton_logs, crash_logs, graphics_logs, openvr_logs;
};
inline constexpr std::string_view debug_wine_channels =
    "+timestamp,+pid,+tid,+seh,+unwind,+threadname,+debugstr,+loaddll,+mscoree,"
    "+process,+wintrust,+crypt,+chain,+openxr,+vrclient,+steamclient";
Env proton_environment(Env env, const ProtonSettings &settings);
// The OpenXR part of launch_environment: the runtime manifest, Proton's
// container import, the OVR compatibility layer and native DXVK.
void apply_openxr_environment(Env &env, const fs::path &runtime_json);
// The OpenVR runtime selected for an OpenVR launch.
void configure_openvr_environment(Env &env, const fs::path &config_dir, const fs::path &rift_runtime,
                                  const fs::path &openvr_runtime, const fs::path &openvr_registry,
                                  std::string_view openvr_kind);

// POSIX shell-like splitting (Python's shlex.split); throws on an unclosed quote.
std::vector<std::string> shell_split(std::string_view text);


} // namespace riftlift::runtime
