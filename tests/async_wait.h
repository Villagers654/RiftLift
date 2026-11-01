#pragma once

#include <chrono>
#include <cstdio>
#include <source_location>
#include <thread>

namespace riftlift::test {
// These waits guard test hangs, not product latency. Keep semantic timeout and
// performance assertions at their call sites, independent of this watchdog.
inline constexpr auto hang_timeout = std::chrono::seconds(120);

template <typename Predicate>
bool wait_until(Predicate ready,
                std::source_location location = std::source_location::current()) {
  const auto started = std::chrono::steady_clock::now();
  const auto deadline = started + hang_timeout;
  bool completed = true;
  while (!ready()) {
    if (std::chrono::steady_clock::now() >= deadline) { completed = false; break; }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  if (!completed)
    std::fprintf(stderr, "[fixture-wait] %s:%u: no progress\n", location.file_name(), location.line());
  return completed;
}
} // namespace riftlift::test
