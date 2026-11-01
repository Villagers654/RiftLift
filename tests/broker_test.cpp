#include "common/process.h"
#include "tests/async_wait.h"
#include "tests/temp_dir.h"
#include <doctest/doctest.h>

using namespace riftlift;
TEST_CASE("inherited credential pipes keep tokens out of argv environment and child logs") {
  test::TempDir d("broker");
  Process child;
  child.spawn(RIFTLIFT_BROKER_CHILD, {"--account", "123456abcdef"},
              d / "child.log", false, [](const Json &request) {
                return str(request, "op") == "credentials"
                           ? Json{{"appToken", "fixture-app-pipe-secret"}}
                           : Json{{"error", 0}};
              });
  REQUIRE(test::wait_until([&] { return child.poll().has_value(); }));
  CHECK(*child.poll() == 0);
  auto log = paths::read(d / "child.log");
  CHECK(log.find("tokenInArgv=0 tokenInEnv=0 tokenFromPipe=1") != std::string::npos);
  CHECK(log.find("fixture-app-pipe-secret") == std::string::npos);
}
