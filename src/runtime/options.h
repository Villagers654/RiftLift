#pragma once
// riftlift-runtime --game <slug> --log <file> --account <key|local>
//                  --parent-pid <pid> [--console] [--run-seconds N]
//                  [--credential-read H --credential-write H]
// riftlift-runtime --exe <game.exe> [--name NAME] [--args ARGS] ...
//                  (a game outside the library, launched as if added)
// riftlift-runtime --check
#include <cstdint>
#include <string>
#include <vector>

namespace riftlift::runtime {

struct Options {
  std::string game, log, account = "local";
  // --exe: the record add would write for this .exe, kept in memory only.
  std::string exe, name, arguments;
  std::uint32_t parent_pid = 0;
  bool console = false;
  std::uint32_t run_seconds = 0;  // 0: until the game exits
  // The launcher's Platform broker (common/ipc.h): inherited pipe handles,
  // 0 when the launcher gave none.
  std::uintptr_t credential_read = 0, credential_write = 0;
  // --check: prints each compatibility component's state and exits (the
  // debug report's checks); reads only.
  bool check = false;
  // Internal: dlopen()s an OpenVR client in a separate process to validate it.
  std::string probe_openvr;
  std::vector<std::string> ignored;  // options this build does not know
};
// Throws std::invalid_argument for a missing or malformed value.
Options parse_options(const std::vector<std::string> &args);

} // namespace riftlift::runtime
