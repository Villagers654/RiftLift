#include "common/rift/game.h"
#include "common/rift/local_games.h"
#include "tests/temp_dir.h"
#include <doctest/doctest.h>

using namespace riftlift;
using riftlift::test::TempDir;

namespace {
rift::Game sample(const fs::path &directory) {
  rift::Game g;
  g.slug = "lone-echo";
  g.name = "Lone Echo";
  g.app_id = "1368187813209608";
  g.app_key = "ready-at-dawn-echo-arena";
  g.directory = directory;
  g.executable = "bin/win7/loneecho.exe";
  g.arguments = {"-vr"};
  return g;
}
} // namespace

TEST_CASE("slugs") {
  CHECK(rift::slugify("Lone Echo: Deluxe") == "lone-echo-deluxe");
  CHECK(rift::slugify("FIVE NIGHTS AT FREDDY'S: HELP WANTED") == "five-nights-at-freddy-s-help-wanted");
  CHECK(rift::slugify("!!!") == "meta-rift-game");
  CHECK(rift::valid_slug("superhot-vr"));
  CHECK_FALSE(rift::valid_slug("Superhot"));
  CHECK_FALSE(rift::valid_slug("-a"));
  CHECK_FALSE(rift::valid_slug("../x"));
  CHECK_FALSE(rift::valid_slug(""));
}

TEST_CASE("launch arguments keep Windows paths and round-trip") {
  CHECK(rift::split_arguments(R"(-log C:\Logs\run.txt -config "C:\Program Files\Game\vr.ini" \\server\share)") ==
        std::vector<std::string>{"-log", R"(C:\Logs\run.txt)", "-config", R"(C:\Program Files\Game\vr.ini)",
                                 R"(\\server\share)"});
  for (const auto &arguments : std::vector<std::vector<std::string>>{
           {"-level", "gpr_020_post_tutorial"},
           {"C:\\Folder With Space\\", "Z:\\data\\"},
           {"", "it's", "say \"hi\"", "both ' and \""}})
    CHECK(rift::split_arguments(rift::join_arguments(arguments)) == arguments);
  CHECK_THROWS(rift::split_arguments("\"unclosed"));
}

TEST_CASE("DLL overrides") {
  CHECK_NOTHROW(rift::validate_dll_overrides(""));
  CHECK_NOTHROW(rift::validate_dll_overrides("version=n,b;winhttp=n,b"));
  CHECK_NOTHROW(rift::validate_dll_overrides("d3d11,dxgi=n; *xinput1_3=b"));
  CHECK_NOTHROW(rift::validate_dll_overrides("mscoree="));
  CHECK_THROWS(rift::validate_dll_overrides("version=oops"));
  CHECK_THROWS(rift::validate_dll_overrides("version"));
  CHECK_THROWS(rift::validate_dll_overrides("bad name=n"));
}

TEST_CASE("game records round-trip and keep unknown fields") {
  TempDir dir("record");
  auto game = sample(dir.path);
  game.launch_options = {"-screen-fullscreen", "0"};
  game.environment = {{"PROTON_LOG", "1"}};
  game.dll_overrides = "version=n,b";
  auto json = game.json();
  json["future_field"] = {{"kept", true}};
  auto parsed = rift::Game::parse(json);
  CHECK(parsed.json() == json);
  CHECK(parsed.launch_options == game.launch_options);

  SUBCASE("saved and loaded from the data folder") {
    TempDir data("data");
    rift::save(data.path, parsed);
    auto loaded = rift::load(data.path, "lone-echo");
    CHECK(loaded.json() == json);
    auto all = rift::load_all(data.path);
    CHECK(all.complete);
    REQUIRE(all.games.size() == 1);
    rift::remove_record(data.path, "lone-echo");
    CHECK(rift::load_all(data.path).games.empty());
  }
  SUBCASE("old records") {
    auto old = json;
    old.erase("source");
    old.erase("launch_options");
    old.erase("dll_overrides");
    old.erase("environment");
    auto loaded = rift::Game::parse(old);
    CHECK(loaded.source == "meta");
    CHECK(loaded.launch_options.empty());
    CHECK(loaded.environment.empty());
    old["app_key"] = "steam.app.617830";
    CHECK(rift::Game::parse(old).source == "steam");
  }
  SUBCASE("invalid fields") {
    auto bad = json;
    bad["executable"] = "../outside.exe";
    CHECK_THROWS(rift::Game::parse(bad));
    bad = json;
    bad["environment"] = {{"export PROTON_LOG", "1"}};
    CHECK_THROWS(rift::Game::parse(bad));
    bad = json;
    bad["dll_overrides"] = "version=oops";
    CHECK_THROWS(rift::Game::parse(bad));
  }
}

TEST_CASE("a record that cannot be read leaves the scan incomplete") {
  TempDir data("data");
  rift::save(data.path, sample(data.path));
  test::write(rift::state_dir(data.path) / "games" / "broken.json", "{");
  auto all = rift::load_all(data.path);
  CHECK_FALSE(all.complete);
  CHECK(all.games.size() == 1);
}

TEST_CASE("local games") {
  TempDir root("local");
  SUBCASE("UE4's bin/win10 layout uses the project folder") {
    test::write(root / "MyGame/bin/win10/MyGame.exe", "MZ");
    auto g = rift::local_game(root / "MyGame/bin/win10/MyGame.exe", "My Game");
    CHECK(g.slug == "my-game");
    CHECK(g.source == "local");
    CHECK(g.directory == fs::weakly_canonical(root / "MyGame"));
    CHECK(g.executable == "bin/win10/MyGame.exe");
    CHECK(g.app_key == "MyGame");
  }
  SUBCASE("a plain folder") {
    test::write(root / "Game/Game.exe", "MZ");
    auto g = rift::local_game(root / "Game/Game.exe", {}, {}, "-vr \"two words\"");
    CHECK(g.name == "Game");
    CHECK(g.app_key == "local.game");
    CHECK(g.arguments == std::vector<std::string>{"-vr", "two words"});
  }
  SUBCASE("only Windows executables") {
    test::write(root / "Game/run.sh", "#!/bin/sh");
    CHECK_THROWS(rift::local_game(root / "Game/run.sh"));
    CHECK_THROWS(rift::local_game(root / "Game/missing.exe"));
  }
  SUBCASE("adding keeps settings and avoids taken slugs") {
    TempDir data("data");
    test::write(root / "Game/Game.exe", "MZ");
    test::write(root / "Other/Game.exe", "MZ");
    auto first = rift::add_game(data.path, rift::local_game(root / "Game/Game.exe"));
    first.launch_options = {"-x"};
    rift::save(data.path, first);
    auto again = rift::add_game(data.path, rift::local_game(root / "Game/Game.exe"));
    CHECK(again.slug == "game");
    CHECK(again.launch_options == std::vector<std::string>{"-x"});
    auto other = rift::add_game(data.path, rift::local_game(root / "Other/Game.exe"));
    CHECK(other.slug == "game-local");
    auto third = rift::add_game(data.path, [&] {
      test::write(root / "Third/Game.exe", "MZ");
      return rift::local_game(root / "Third/Game.exe");
    }());
    CHECK(third.slug == "game-local-2");
  }
}

TEST_CASE("Steam's text KeyValues") {
  auto parsed = rift::parse_text_vdf("// heading\n\"users\" { \"123\" { \"Name\" \"A \\\"quoted\\\" name\" "
                                     "\"Path\" \"C:\\Games\" \"Escaped\" \"C:\\\\Program Files\" } }");
  CHECK(parsed == Json{{"users", {{"123", {{"Name", "A \"quoted\" name"}, {"Path", "C:\\Games"},
                                            {"Escaped", "C:\\Program Files"}}}}}});
  CHECK_THROWS(rift::parse_text_vdf("\"a\" { \"b\" \"c\""));
  CHECK_THROWS(rift::parse_text_vdf("}"));
}

TEST_CASE("Steam library folders") {
  TempDir steam("steam");
  TempDir other("library");
  fs::create_directories(other / "steamapps");
  test::write(steam / "steamapps/libraryfolders.vdf",
              "\"libraryfolders\" { \"0\" { \"path\" \"" + paths::utf8(steam.path) + "\" } \"1\" { \"path\" \"" +
                  paths::utf8(other.path) + "\" } }");
  auto roots = rift::steam_library_roots(steam.path);
  REQUIRE(roots.size() == 2);
  CHECK(roots[1] == fs::weakly_canonical(other.path));
  // A game without the Oculus runtime is not listed.
  test::write(steam / "steamapps/appmanifest_10.acf",
              "\"AppState\" { \"appid\" \"10\" \"name\" \"Flat Game\" \"installdir\" \"Flat\" }");
  test::write(steam / "steamapps/common/Flat/Flat.exe", "MZ");
  CHECK(rift::steam_oculus_games(steam.path).empty());
}
