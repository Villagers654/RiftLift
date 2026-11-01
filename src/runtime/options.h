#pragma once
// riftlift-runtime --game <slug> --log <file> --account <key|local>
//                  --parent-pid <pid> [--console] [--run-seconds N]
#include <cstdint>
#include <string>
#include <vector>

namespace riftlift::runtime {

struct Options {
  std::string game, log, account = "local";
  std::uint32_t parent_pid = 0;
  bool console = false;
  std::uint32_t run_seconds = 0;  // 0: until the game exits
  // Internal: dlopen()s an OpenVR client in a separate process to validate it.
  std::string probe_openvr;
  std::vector<std::string> ignored;  // options this build does not know
};
// Throws std::invalid_argument for a missing or malformed value.
Options parse_options(const std::vector<std::string> &args);

} // namespace riftlift::runtime
