#pragma once
// What a Windows game install is, read from its files: which executable is
// the real game process and which VR APIs and engine it uses. The runtime
// picks its translation path from these; no title list is kept.
#include "common/paths.h"
#include <optional>
#include <set>
namespace riftlift::rift {
bool is_pe64(const fs::path &file);
// Lower-case DLL names from the normal and delay-load import tables, or
// nothing when the file is not a readable PE image.
std::optional<std::set<std::string>> imported_dlls(const fs::path &file);
bool uses_oculus_runtime(const fs::path &directory);
bool uses_openvr_runtime(const fs::path &directory);
// OculusXRPlugin.dll or OVRPlugin.dll: Unity's native Oculus integrations.
bool uses_unity_oculus_plugin(const fs::path &directory);
bool uses_d3d12_runtime(const fs::path &executable);
bool is_unreal_shipping(const fs::path &executable);
bool is_unity_player(const fs::path &executable);
// The shipping binary an Unreal bootstrapper starts, if `executable` is one:
// the child would run without the runtime, so launches target it directly.
std::optional<fs::path> unreal_shipping_for_bootstrap(const fs::path &executable);
// The actual 64-bit game process, avoiding engine bootstrappers, crash
// reporters and installers. `preferred` is relative to `directory`.
fs::path best_windows_executable(const fs::path &directory, const fs::path &preferred = {});
} // namespace riftlift::rift
