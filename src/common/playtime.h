#pragma once
// Local playtime per game in <data>/playtime.json, shared by every launcher
// process (window, command line, direct launches) under a file lock.
// Nothing is sent anywhere.
#include "common/paths.h"
#include <chrono>
#include <optional>
namespace riftlift::playtime {
struct Record {
  double seconds = 0;
  std::uint64_t launches = 0;
  std::string last_played_at;  // ISO 8601 UTC; empty when never played
  // last_played_at in seconds since 1970, 0 when never played.
  std::uint64_t last_played_unix() const;
};
Record get(const fs::path &data, std::string_view game);
// Counts a launch now.
void mark_launch(const fs::path &data, std::string_view game);
void add(const fs::path &data, std::string_view game, double seconds);
// "< 1m", "45m", "2h", "2h 5m".
std::string format(double seconds);
// A running game's time, saved every checkpoint and when it ends, so a crash
// loses at most one interval.
class Session {
public:
  using Clock = std::chrono::steady_clock;
  Session(fs::path data, std::string game);
  ~Session();
  Session(const Session &) = delete;
  Session &operator=(const Session &) = delete;
  // Saves the time since the last save once `interval` has passed.
  void tick(Clock::duration interval = std::chrono::seconds(30));
  void close();

private:
  fs::path data_;
  std::string game_;
  Clock::time_point last_;
  bool closed_ = false;
  void save(Clock::time_point now);
};
} // namespace riftlift::playtime
