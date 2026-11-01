// riftlift-runtime: prepares the compatibility layer for one game, runs it and
// exits with its exit code. Started by the launcher (see options.h); a failed
// launch writes one "[runtime] <reason>" error line, which the launcher shows.
#include "common/log.h"
#include "common/paths.h"
#include "common/rift/game.h"
#include "runtime/launch.h"
#include "runtime/layout.h"
#include "runtime/options.h"
#include "runtime/platform_relay.h"
#include <atomic>
#include <limits>
#include <thread>
#ifdef _WIN32
#include <windows.h>
#include <shellapi.h>
#else
#include <cerrno>
#include <csignal>
#include <unistd.h>
#endif

using namespace riftlift;
using namespace std::chrono_literals;

namespace {
#ifndef _WIN32
std::atomic<bool> terminate_requested{false};
extern "C" void on_signal(int) { terminate_requested = true; }
#endif

std::vector<std::string> arguments(int argc, char **argv) {
  std::vector<std::string> args;
#ifdef _WIN32
  (void)argc, (void)argv;
  int count = 0;
  if (auto **wide = CommandLineToArgvW(GetCommandLineW(), &count)) {
    for (int i = 1; i < count; ++i)
      args.push_back(paths::utf8(fs::path(wide[i])));
    LocalFree(wide);
  }
#else
  for (int i = 1; i < argc; ++i)
    args.emplace_back(argv[i]);
#endif
  return args;
}
log::Level level_from_environment() {
  const char *value = std::getenv("RIFTLIFT_LOG_LEVEL");
  if (value && value[0] >= '0' && value[0] <= '4' && !value[1])
    return log::Level(value[0] - '0');
  return log::Level::info;
}
std::string one_line(std::string text) {
  for (auto &c : text)
    if (c == '\n' || c == '\r')
      c = ' ';
  return text;
}

// Turns SIGTERM / the launcher's stop event, the launcher's exit and the
// --run-seconds deadline into a stop request for the launch.
class Watcher {
public:
  Watcher(std::uint32_t parent, std::uint32_t seconds) : seconds_(seconds) {
#ifdef _WIN32
    // The name process.cpp's Process::request_stop() opens.
    auto name = "Local\\RiftLift.Stop." + std::to_string(GetCurrentProcessId());
    stop_event_ = CreateEventA(nullptr, TRUE, FALSE, name.c_str());
    if (parent)
      parent_ = OpenProcess(SYNCHRONIZE, FALSE, parent);
#else
    struct sigaction action{};
    action.sa_handler = on_signal;
    sigemptyset(&action.sa_mask);
    for (int signal : {SIGTERM, SIGINT, SIGHUP})
      sigaction(signal, &action, nullptr);
    std::signal(SIGPIPE, SIG_IGN);
#endif
    thread_ = std::jthread([this, parent](std::stop_token done) {
      while (!done.stop_requested() && !source_.stop_requested()) {
#ifdef _WIN32
        (void)parent;  // parent_ holds its handle
        HANDLE handles[2];
        DWORD count = 0;
        if (stop_event_) handles[count++] = stop_event_;
        if (parent_) handles[count++] = parent_;
        DWORD result = count ? WaitForMultipleObjects(count, handles, FALSE, 100) : (Sleep(100), WAIT_TIMEOUT);
        if (result < WAIT_OBJECT_0 + count) {
          if (handles[result - WAIT_OBJECT_0] == parent_)
            log::warn("runtime", "The launcher exited; stopping the game.");
          else
            log::info("runtime", "Stop requested.");
          source_.request_stop();
        }
#else
        std::this_thread::sleep_for(100ms);
        if (terminate_requested) {
          log::info("runtime", "Stop requested.");
          source_.request_stop();
        } else if (parent && ::kill(pid_t(parent), 0) != 0 && errno == ESRCH) {
          log::warn("runtime", "The launcher exited; stopping the game.");
          source_.request_stop();
        }
#endif
        if (!source_.stop_requested() && std::chrono::steady_clock::now().time_since_epoch().count() >=
                                             deadline_.load()) {
          log::info("runtime", "Run time is over; stopping the game.");
          timed_out_ = true;
          source_.request_stop();
        }
      }
    });
  }
  ~Watcher() {
    thread_.request_stop();
    thread_.join();
#ifdef _WIN32
    if (stop_event_) CloseHandle(stop_event_);
    if (parent_) CloseHandle(parent_);
#endif
  }
  std::stop_token token() const { return source_.get_token(); }
  // --run-seconds counts from the game's start, not from setup.
  void game_started() {
    if (seconds_)
      deadline_ = (std::chrono::steady_clock::now() + std::chrono::seconds(seconds_)).time_since_epoch().count();
  }
  bool timed_out() const { return timed_out_; }

private:
  std::stop_source source_;
  std::atomic<bool> timed_out_{false};
  std::uint32_t seconds_;
  std::atomic<std::chrono::steady_clock::rep> deadline_{std::numeric_limits<std::chrono::steady_clock::rep>::max()};
#ifdef _WIN32
  HANDLE stop_event_ = nullptr, parent_ = nullptr;
#endif
  std::jthread thread_;
};
} // namespace

int main(int argc, char **argv) {
  runtime::Options options;
  try {
    options = runtime::parse_options(arguments(argc, argv));
  } catch (const std::exception &e) {
    log::error("runtime", "{}", e.what());
    return 2;
  }
#ifndef _WIN32
  if (!options.probe_openvr.empty())
    return runtime::probe_openvr(options.probe_openvr);
#endif
  log::set_level(level_from_environment());
  if (options.console)
    log::attach_console();
  if (!options.log.empty())
    log::set_file(options.log);
  int code = 1;
  {
    Watcher watcher(options.parent_pid, options.run_seconds);
    try {
      for (auto &ignored : options.ignored)
        log::debug("runtime", "Ignoring option {}", ignored);
      runtime::LaunchRequest request;
      request.options = options;
      request.root = paths::data_dir();  // honours RIFTLIFT_DATA_DIR
      request.game = rift::load(request.root, options.game);
      request.env = runtime::current_environment();
      // The game reaches the launcher's Platform broker through this relay;
      // without a broker its Platform calls answer that Meta is unavailable.
      std::unique_ptr<runtime::PlatformRelay> relay;
      request.env.erase("RIFTLIFT_PLATFORM_BROKER");
      if (options.credential_read) {
        relay = std::make_unique<runtime::PlatformRelay>(
            PipeChannel::inherited(options.credential_read, options.credential_write));
        request.env["RIFTLIFT_PLATFORM_BROKER"] = relay->endpoint();
      }
      request.stop = watcher.token();
      request.started = [&watcher] { watcher.game_started(); };
      log::info("runtime", "RiftLift runtime {} starting {} (account {}).", runtime::payload_version,
                options.game, options.account);
      code = runtime::launch(request);
      // A smoke run whose game was still running at the deadline passed.
      if (watcher.timed_out())
        code = 0;
    } catch (const std::exception &e) {
      log::error("runtime", "{}", one_line(e.what()));
      code = 1;
    }
  }
  log::flush();
  return code;
}
