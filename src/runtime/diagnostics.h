#pragma once
// The bounded debug-log folders RiftLift's debug logging writes into
// (<data>/diagnostics/...), kept at the sizes the Python app used.
#include "runtime/layout.h"
#include <cstdint>

namespace riftlift::runtime {

struct DebugLogs {
  fs::path proton, graphics, crashes, openvr, game;
};
// Creates (0700) and prunes each folder, as prepare_debug_logs did.
DebugLogs prepare_debug_logs(const Layout &layout);
fs::path prepare_proton_logs(const Layout &layout);

// Keeps the head quarter and the tail of a log larger than `max_bytes`.
void trim_diagnostic_log(const fs::path &file, std::uintmax_t max_bytes);

// The OpenXR/OpenVR bridge trace files inside the Wine prefix.
std::vector<fs::path> runtime_trace_paths(const Layout &layout);
void clear_runtime_traces(const Layout &layout);
void trim_runtime_traces(const Layout &layout);

} // namespace riftlift::runtime
