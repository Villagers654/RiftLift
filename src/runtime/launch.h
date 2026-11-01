#pragma once
// One game launch, from a loaded record to the game's exit code.
#include "common/rift/game.h"
#include "runtime/child.h"
#include "runtime/options.h"
#include <functional>
#include <stop_token>

namespace riftlift::runtime {

struct LaunchRequest {
  Options options;
  fs::path root;  // RIFTLIFT_DATA_DIR or the default data folder
  rift::Game game;
  Env env;  // this process's environment
  // Requested by SIGTERM / the stop event, the parent's exit or --run-seconds:
  // the game is stopped and its exit code returned.
  std::stop_token stop;
  // Called once the game process is running (starts --run-seconds).
  std::function<void()> started = [] {};
};
// Throws for a launch that could not start; the message becomes the
// "[runtime]" error line the launcher shows.
int launch(const LaunchRequest &request);
// --probe-openvr: loads an OpenVR client library; 0 when it is usable.
int probe_openvr(const std::string &library);

} // namespace riftlift::runtime
