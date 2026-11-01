#include "common/playtime.h"
#include "common/file_ops.h"
#include "common/log.h"
#include <cstdio>
#include <format>
namespace riftlift::playtime {
namespace {
Json read_all(const fs::path &data) {
  try {
    auto file = data / "playtime.json";
    if (fs::exists(file)) {
      auto j = paths::read_json(file);
      if (field(j, "games").is_object())
        return j;
    }
  } catch (...) {
  }
  return Json{{"version", 1}, {"games", Json::object()}};
}
double seconds_of(const Json &record) {
  const auto &value = field(record, "seconds");
  return value.is_number() ? std::max(0.0, value.get<double>()) : 0.0;
}
std::uint64_t launches_of(const Json &record) {
  const auto &value = field(record, "launches");
  return value.is_number_unsigned() ? value.get<std::uint64_t>() : 0;
}
template <typename Fn> void update(const fs::path &data, std::string_view game, Fn &&change) {
  fs::create_directories(data);
  FileLock lock(data / "playtime.lock");
  auto all = read_all(data);
  auto &record = all["games"][std::string(game)];
  if (!record.is_object())
    record = Json::object();
  change(record);
  all["version"] = 1;
  paths::atomic_write(data / "playtime.json", all.dump(2) + "\n");
}
} // namespace
Record get(const fs::path &data, std::string_view game) {
  const auto all = read_all(data);
  const auto &record = field(field(all, "games"), game);
  return {seconds_of(record), launches_of(record), str(record, "last_played_at")};
}
void mark_launch(const fs::path &data, std::string_view game) {
  update(data, game, [](Json &record) {
    record["launches"] = launches_of(record) + 1;
    record["last_played_at"] = std::format("{:%FT%T}+00:00", std::chrono::floor<std::chrono::seconds>(
                                                                 std::chrono::system_clock::now()));
    record["seconds"] = seconds_of(record);
  });
}
void add(const fs::path &data, std::string_view game, double seconds) {
  if (!(seconds > 0))
    return;
  update(data, game, [&](Json &record) {
    record["seconds"] = seconds_of(record) + seconds;
    record["launches"] = launches_of(record);
    if (!record.contains("last_played_at"))
      record["last_played_at"] = "";
  });
}
std::uint64_t Record::last_played_unix() const {
  // Written as "YYYY-MM-DDTHH:MM:SS+00:00" (Python's isoformat, and mark_launch).
  int y = 0, mo = 0, d = 0, h = 0, mi = 0, s = 0;
  if (std::sscanf(last_played_at.c_str(), "%d-%d-%dT%d:%d:%d", &y, &mo, &d, &h, &mi, &s) != 6)
    return 0;
  namespace c = std::chrono;
  auto day = c::year_month_day{c::year(y), c::month(unsigned(mo)), c::day(unsigned(d))};
  if (!day.ok())
    return 0;
  auto at = c::sys_days(day) + c::hours(h) + c::minutes(mi) + c::seconds(s);
  auto since = c::duration_cast<c::seconds>(at.time_since_epoch()).count();
  return since > 0 ? std::uint64_t(since) : 0;
}
std::string format(double seconds) {
  auto minutes = std::uint64_t(std::max(0.0, seconds) / 60);
  if (!minutes)
    return "< 1m";
  auto hours = minutes / 60;
  minutes %= 60;
  if (hours && minutes)
    return std::format("{}h {}m", hours, minutes);
  return hours ? std::format("{}h", hours) : std::format("{}m", minutes);
}
Session::Session(fs::path data, std::string game)
    : data_(std::move(data)), game_(std::move(game)), last_(Clock::now()) {
  mark_launch(data_, game_);
}
Session::~Session() {
  try {
    close();
  } catch (...) {
  }
}
void Session::save(Clock::time_point now) {
  add(data_, game_, std::chrono::duration<double>(now - last_).count());
  last_ = now;
}
void Session::tick(Clock::duration interval) {
  auto now = Clock::now();
  if (closed_ || now - last_ < interval)
    return;
  try {
    save(now);
  } catch (const std::exception &e) {
    log::warn("playtime", "Could not save playtime for {}: {}", game_, e.what());
  }
}
void Session::close() {
  if (closed_)
    return;
  closed_ = true;
  save(Clock::now());
}
} // namespace riftlift::playtime
