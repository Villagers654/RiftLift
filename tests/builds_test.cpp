#include "common/library/library.h"
#include "common/meta/store.h"
#include <doctest/doctest.h>
using namespace riftlift;

namespace {
Json history() {
  return Json::parse(R"({"data":{"node":{"primary_binaries":{"nodes":[
    {"__typename":"PCBinary","id":"300","version":"1.2","version_code":12,
     "binary_release_channels":{"nodes":[{"channel_name":"LIVE"}]}},
    {"__typename":"PCBinary","id":"400","version":"1.3-beta","version_code":13,
     "binary_release_channels":{"nodes":[{"channel_name":"BETA"}]}},
    {"__typename":"PCBinary","id":"100","version":"1.0","version_code":10,
     "binary_release_channels":{"nodes":[{"channel_name":"LIVE"},{"channel_name":"OLD"}]}},
    {"__typename":"PCBinary","id":"999","version":"internal","version_code":99,
     "binary_release_channels":{"nodes":[]}},
    {"__typename":"AndroidBinary","id":"555","version":"quest","version_code":50,
     "binary_release_channels":{"nodes":[{"channel_name":"LIVE"}]}}
  ]}}}})");
}
} // namespace

TEST_CASE("build history keeps published PC builds only") {
  auto list = meta::parse_history(history());
  REQUIRE(list.size() == 3);
  CHECK(list[0].binary_id == "300");
  CHECK(list[0].version_code == "12");
  CHECK(list[2].channels == std::vector<std::string>{"LIVE", "OLD"});
  CHECK_FALSE(list[0].offered);
  CHECK_THROWS(meta::parse_history(Json::parse(R"({"data":{"node":null}})")));
}

TEST_CASE("offered builds and history merge, newest first, default stays offered") {
  std::vector<meta::Build> offered{{"300", "1.2", "12", "Fixes", {}, true}};
  auto list = meta::merge_builds(offered, meta::parse_history(history()));
  REQUIRE(list.size() == 3);
  CHECK(list[0].binary_id == "400");  // the beta is newer, but not offered
  CHECK_FALSE(list[0].offered);
  CHECK(list[1].binary_id == "300");
  CHECK(list[1].offered);
  CHECK(list[1].change_log == "Fixes");
  CHECK(list[1].channels == std::vector<std::string>{"LIVE"});
  CHECK(std::ranges::find_if(list, &meta::Build::offered)->binary_id == "300");
  CHECK(list[2].label() == "1.0 (10)");
}

TEST_CASE("a chosen version is its own library entry") {
  CHECK(library::version_id("1368187813209608", "300") == "1368187813209608@300");
  CHECK(library::base_app("1368187813209608@300") == "1368187813209608");
  CHECK(library::base_app("1368187813209608") == "1368187813209608");
  rift::Game g;
  g.slug = "lone-echo-1-0";
  g.name = "Lone Echo (1.0)";
  g.app_id = "1368187813209608";
  g.binary_id = "100";
  g.pinned = true;
  g.directory = fs::temp_directory_path() / "lone-echo-1-0";
  g.executable = "LoneEcho.exe";
  CHECK(library::from_record(g).plan.app_id == "1368187813209608@100");
  CHECK(rift::Game::parse(g.json()).pinned);
  g.pinned = false;
  CHECK(library::from_record(g).plan.app_id == "1368187813209608");
  CHECK_FALSE(g.json().contains("pinned"));
}
