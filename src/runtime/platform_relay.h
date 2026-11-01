#pragma once
// The game's way to the launcher's Platform broker (common/meta/platform.h).
// The platform shim inside the game (compat/riftlift-platform-shim.c) cannot
// use this process's inherited pipe, so the runtime listens on a loopback port
// and forwards each of its requests. Only the shim's few operations pass, and
// only from a client holding this launch's secret.
#include "common/ipc.h"
#include <functional>
#include <memory>
#include <string>
#include <thread>

namespace riftlift::runtime {

class PlatformRelay {
public:
  // Starts listening; throws when no loopback port can be opened.
  explicit PlatformRelay(std::shared_ptr<PipeChannel> launcher);
  ~PlatformRelay();
  PlatformRelay(const PlatformRelay &) = delete;
  PlatformRelay &operator=(const PlatformRelay &) = delete;
  // RIFTLIFT_PLATFORM_BROKER for the game: "127.0.0.1:<port> <secret>".
  std::string endpoint() const;
  // One request from the shim -> the reply it gets. `call` asks the launcher.
  static Json forward(const Json &request, std::string_view secret,
                      const std::function<Json(const Json &)> &call);

private:
  std::shared_ptr<PipeChannel> launcher_;
  std::string secret_;
  std::uintptr_t socket_ = 0;
  std::uint16_t port_ = 0;
  std::jthread thread_;
  void serve(std::stop_token stop);
};

} // namespace riftlift::runtime
