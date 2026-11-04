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

#ifndef _WIN32
TEST_CASE("a game in a library folder reached through a link uninstalls") {
  test::TempDir data("library");
  test::TempDir drive("drive");
  test::TempDir home("home");
  fs::create_directories(drive / "Games");
  fs::create_directory_symlink(drive / "Games", home / "Games");  // the folder moved to another drive
  rift::Game meta;
  meta.slug = "lone-echo";
  meta.name = "Lone Echo";
  meta.source = "meta";
  meta.directory = home / "Games/lone-echo";
  meta.executable = "LoneEcho.exe";
  test::write(meta.directory / "LoneEcho.exe", "MZ");
  test::write(meta.directory / "manifest.json", "{}");
  rift::save(data.path, meta);
  library::uninstall(data.path, library::from_record(meta));
  CHECK_FALSE(fs::exists(drive / "Games/lone-echo"));
  CHECK(fs::is_directory(drive / "Games"));
  CHECK(rift::load_all(data.path).games.empty());

  // A link below the library folder is still refused.
  test::write(drive / "elsewhere/LoneEcho.exe", "MZ");
  test::write(drive / "elsewhere/manifest.json", "{}");
  fs::create_directory_symlink(drive / "elsewhere", home / "Games/lone-echo");
  rift::save(data.path, meta);
  CHECK_THROWS(library::uninstall(data.path, library::from_record(meta)));
  CHECK(fs::exists(drive / "elsewhere/LoneEcho.exe"));
}
#endif

TEST_CASE("a crash right after a signed-out platform reply asks for a sign-in") {
  test::TempDir dir("signed-out");
  auto log = dir / "runtime.log";
  test::write(log, "  1.000 1 I [platform] Game asked for user_proof: error -20001.\n"
                   "  4.500 1 I [runtime] Game exited with code -1073741819.\n");
  CHECK(library::crashed_signed_out(log));
  // Long after the reply, the crash is something else.
  test::write(log, "  1.000 1 I [platform] Game asked for user_proof: error -20001.\n"
                   " 95.000 1 I [runtime] Game exited with code 1.\n");
  CHECK_FALSE(library::crashed_signed_out(log));
  // Answered, or only the entitlement check (handled on its own).
  test::write(log, "  1.000 1 I [platform] Game asked for credentials: error 0.\n"
                   "  2.000 1 I [runtime] Game exited with code 1.\n");
  CHECK_FALSE(library::crashed_signed_out(log));
  test::write(log, "  1.000 1 I [platform] Game asked for entitlement: error -20001.\n"
                   "  2.000 1 I [runtime] Game exited with code 1.\n");
  CHECK_FALSE(library::crashed_signed_out(log));
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
