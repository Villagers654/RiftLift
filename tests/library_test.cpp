#include "common/download/manager.h"
#include "common/library/library.h"
#include "tests/temp_dir.h"
#include <doctest/doctest.h>

using namespace riftlift;

TEST_CASE("installs from records") {
  test::TempDir data("library");
  test::TempDir games("games");
  rift::Game store;
  store.slug = "lone-echo";
  store.name = "Lone Echo";
  store.app_id = "1368187813209608";
  store.directory = games / "lone-echo";
  store.executable = "bin/win7/loneecho.exe";
  rift::save(data.path, store);
  test::write(games / "lone-echo/bin/win7/loneecho.exe", "MZ1234");

  rift::Game steam = store;
  steam.slug = "superhot-vr";
  steam.name = "SUPERHOT VR";
  steam.app_id = "617830";
  steam.source = "steam";
  steam.directory = games / "superhot";
  steam.executable = "SUPERHOTVR.exe";
  rift::save(data.path, steam);

  bool reliable = false;
  auto installs = library::scan(data.path, &reliable);
  CHECK(reliable);
  REQUIRE(installs.size() == 2);
  auto &a = installs[0].game.slug == "lone-echo" ? installs[0] : installs[1];
  auto &b = installs[0].game.slug == "lone-echo" ? installs[1] : installs[0];
  CHECK(a.plan.app_id == "1368187813209608");
  CHECK_FALSE(a.plan.local);
  CHECK(a.complete);
  CHECK(a.bytes == 6);
  // Steam and local games are keyed by their slug; a missing .exe is incomplete.
  CHECK(b.plan.app_id == "superhot-vr");
  CHECK(b.plan.local);
  CHECK_FALSE(b.complete);
}

TEST_CASE("uninstalling a Steam game keeps its files") {
  test::TempDir data("library");
  test::TempDir games("games");
  rift::Game steam;
  steam.slug = "superhot-vr";
  steam.name = "SUPERHOT VR";
  steam.source = "steam";
  steam.directory = games / "superhot";
  steam.executable = "SUPERHOTVR.exe";
  test::write(games / "superhot/SUPERHOTVR.exe", "MZ");
  rift::save(data.path, steam);
  library::uninstall(data.path, library::from_record(steam));
  CHECK(fs::exists(games / "superhot/SUPERHOTVR.exe"));
  CHECK(rift::load_all(data.path).games.empty());
}

TEST_CASE("install folders stay inside their root") {
  test::TempDir games("games");
  CHECK(download::install_dir(games.path, "lone-echo") == games / "lone-echo");
  CHECK_THROWS(download::install_dir(games.path, "../x"));
  CHECK_THROWS(download::install_dir("relative", "lone-echo"));
}

TEST_CASE("the game's last entitlement answer is read from its log") {
  test::TempDir dir("entitlement");
  auto log = dir / "runtime.log";
  CHECK(library::entitlement_error(log) == 0);
  test::write(log, "[platform] Game asked for credentials: error 0.\n"
                   "[platform] Game asked for entitlement: error -20001.\n"
                   "other line\n"
                   "[platform] Game asked for entitlement: error -20002.\n");
  CHECK(library::entitlement_error(log) == -20002);
  test::write(log, "[platform] Game asked for entitlement: error 0.\n");
  CHECK(library::entitlement_error(log) == 0);
}
