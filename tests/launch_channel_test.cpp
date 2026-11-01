#include "common/process.h"
#include "tests/async_wait.h"
#include "tests/temp_dir.h"
#include <doctest/doctest.h>
#include <future>
#include <thread>

using namespace riftlift;
TEST_CASE("launcher pipe preserves frames across an inherited child process") {
  const Json expected{{"type", 4}, {"message", "message with spaces & unicode \xc3\xa9"},
                      {"destination", "lobby"}, {"count", 17}};
  test::TempDir d("launch-pipe");
  Process process;
  std::promise<Json> received;
  auto request = received.get_future();
  process.spawn(RIFTLIFT_LAUNCH_CHANNEL_CHILD, {}, d / "child.log", false, [&](const Json& frame) -> Json {
    if (frame.at("op") == "details") return {{"details", expected}};
    received.set_value(frame);
    return {{"success", true}};
  });
  const auto ready = request.wait_for(test::hang_timeout);
  INFO("request ready=", ready == std::future_status::ready, " child exited=", process.poll().has_value());
  REQUIRE(ready == std::future_status::ready);
  const auto frame = request.get();
  CHECK(frame.at("op") == "echo");
  CHECK(frame.at("app") == "123");
  CHECK(frame.at("details") == expected);
  REQUIRE(test::wait_until([&] { return process.poll().has_value(); }));
  CHECK(*process.poll() == 0);
}

TEST_CASE("pipe channel pairs answer in order and refuse calls once closed") {
  auto [a, b] = PipeChannel::pair();
  std::jthread worker([b](std::stop_token stop) { b->serve([](const Json& value) { return value; }, stop); });
  CHECK(a->call({{"op", "first"}}).at("op") == "first");
  CHECK(a->call({{"op", "second"}}).at("op") == "second");
  a->close();
  worker.join();
  CHECK_THROWS(a->call({{"op", "closed"}}));
}
