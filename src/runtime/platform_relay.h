#pragma once
// The game's way to the launcher's Platform broker (common/meta/platform.h).
// The platform shim inside the game (compat/riftlift-platform-shim.c) cannot
// use this process's inherited pipe, so the runtime listens on a loopback port
// and forwards each of its requests. Only the shim's few operations pass, and
// only from a client holding this launch's secret.
#include "common/ipc.h"
#include <atomic>
#include <condition_variable>
#include <functional>
#include <list>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

namespace riftlift::runtime {

class PlatformRelay {
public:
  // Starts listening; throws when no loopback port can be opened. With the
  // game's `app_id`, its entitlement is asked of Meta at once: some games
  // (Lone Echo) wait only a second for that answer, less than Meta takes, so
  // their first check gets this one. Later checks are asked when made.
  explicit PlatformRelay(std::shared_ptr<PipeChannel> launcher, std::string app_id = {});
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
  // One thread per connection, so an answer at hand never waits behind a
  // request still with Meta.
  struct Client {
    std::jthread thread;
    std::shared_ptr<std::atomic<bool>> done;
  };
  std::list<Client> clients_;
  // The entitlement asked at launch, until the game's first check takes it.
  enum class Early { none, pending, ready, taken };
  std::mutex early_mutex_;
  std::condition_variable early_ready_;
  Early early_ = Early::none;
  std::string early_app_;
  Json early_reply_;
  std::jthread early_thread_;
  void serve(std::stop_token stop);
  void answer_client(std::uintptr_t client);
  Json ask(const Json &request);
};

} // namespace riftlift::runtime
