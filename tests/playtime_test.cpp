#include "common/playtime.h"
#include "tests/temp_dir.h"
#include <doctest/doctest.h>

using namespace riftlift;

TEST_CASE("playtime text") {
  CHECK(playtime::format(0) == "< 1m");
  CHECK(playtime::format(59) == "< 1m");
  CHECK(playtime::format(45 * 60) == "45m");
  CHECK(playtime::format(2 * 3600) == "2h");
  CHECK(playtime::format(2 * 3600 + 5 * 60 + 30) == "2h 5m");
}

TEST_CASE("playtime records") {
  test::TempDir data("playtime");
  CHECK(playtime::get(data.path, "game").launches == 0);
  playtime::mark_launch(data.path, "game");
  playtime::add(data.path, "game", 90.5);
  playtime::add(data.path, "game", -4);
  auto r = playtime::get(data.path, "game");
  CHECK(r.launches == 1);
  CHECK(r.seconds == doctest::Approx(90.5));
  CHECK(r.last_played_unix() > 1700000000);
  CHECK(playtime::get(data.path, "other").seconds == 0);
}

TEST_CASE("files written by the Python release load") {
  test::TempDir data("playtime");
  test::write(data / "playtime.json",
              R"({"version": 1, "games": {"lone-echo": {"launches": 13,
                 "last_played_at": "2026-10-10T03:35:27+00:00", "seconds": 1100.1885696139918}}})");
  auto r = playtime::get(data.path, "lone-echo");
  CHECK(r.launches == 13);
  CHECK(playtime::format(r.seconds) == "18m");
  CHECK(r.last_played_unix() == 1791603327);
  CHECK(playtime::Record{.last_played_at = "never"}.last_played_unix() == 0);
}

TEST_CASE("a session saves on close") {
  test::TempDir data("playtime");
  {
    playtime::Session session(data.path, "game");
    session.close();
  }
  CHECK(playtime::get(data.path, "game").launches == 1);
}
