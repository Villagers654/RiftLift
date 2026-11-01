#pragma once
#include "common/paths.h"
#include <stop_token>
#include <string>
#include <vector>

namespace riftlift::launcher {
struct DebugReport {
  fs::path data;            // RiftLift's data folder
  std::string version;      // the launcher's version
  std::string package;      // the game; empty picks the most recent launch
  std::string game;         // its name, when known
  fs::path plan;            // its game record, when installed
  fs::path runtime;         // riftlift-runtime, asked for its component checks
  bool debug_logging = false;  // Settings > Troubleshooting > Debug logging
  // Text removed wherever it appears: account ids, keys, aliases and names.
  std::vector<std::string> private_text;
};
// Writes one plain-text report about a game's last launch into
// `data`/reports and returns its path:
// - what happened (result, likely cause, next steps);
// - the runtime's component checks;
// - the system, the OpenXR, OpenVR and Vulkan setup and the library;
// - the errors in every log written during the run: Proton, graphics,
//   XRizer, the game's own logs, Steam, WiVRn and Envision logs, and the
//   system journal and crash records;
// - the run's logs and the launcher's.
// The home folder, user and computer names, account details and anything
// shaped like a token are removed; credentials and crash dumps are never read.
fs::path write_debug_report(const DebugReport &request);
// Uploads a written report to dpaste.com as an unlisted paste that expires
// after 90 days and returns its link. Throws when it cannot.
std::string upload_debug_report(const fs::path &file, std::stop_token stop = {});
// Exposed for tests.
std::string redact(std::string text, const std::vector<std::string> &private_text);
// The relevance of one Proton log line (0: leave out), as the doctor ranked them.
int proton_line_priority(std::string_view line);
} // namespace riftlift::launcher
