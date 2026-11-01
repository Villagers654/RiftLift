#include "runtime/rules.h"
#include <doctest/doctest.h>
#include <fstream>

using namespace riftlift;
using namespace riftlift::runtime;

TEST_CASE("runtime: Wine paths use drive Z with backslashes") {
#ifndef _WIN32
  CHECK(wine_path("/home/user/Games/Lone Echo") == "Z:\\home\\user\\Games\\Lone Echo");
  CHECK(wine_path("/") == "Z:\\");
#endif
  CHECK(wine_path("relative/path") == "relative/path");
}

TEST_CASE("runtime: per-game DLL overrides replace inherited rules for the same DLLs") {
  Env env{{"WINEDLLOVERRIDES", "d3d11=n;dxgi,winhttp=n;version=b"}};
  apply_dll_overrides(env, " winhttp.dll=n,b ; ");
  CHECK(env["WINEDLLOVERRIDES"] == "d3d11=n;dxgi=n;version=b;winhttp.dll=n,b");
  apply_dll_overrides(env, "*=b");
  CHECK(env["WINEDLLOVERRIDES"] == "*=b");
  Env unchanged{{"WINEDLLOVERRIDES", "a=n"}};
  apply_dll_overrides(unchanged, " ; ");
  CHECK(unchanged["WINEDLLOVERRIDES"] == "a=n");
  CHECK_THROWS_AS(apply_dll_overrides(unchanged, "version"), std::invalid_argument);
  Env empty;
  apply_dll_overrides(empty, "*version=n");
  CHECK(empty["WINEDLLOVERRIDES"] == "*version=n");
}

TEST_CASE("runtime: mod loader proxies load native first unless named explicitly") {
  auto dir = fs::temp_directory_path() / "riftlift-runtime-test-mods";
  fs::remove_all(dir);
  fs::create_directories(dir / "MelonLoader");
  for (auto name : {"Game.exe", "version.dll", "winmm.dll"})
    std::ofstream(dir / name) << "x";
  Env env{{"WINEDLLOVERRIDES", "d3d11=n;dxgi=n;"}};
  configure_mod_loaders(env, dir / "Game.exe");
  CHECK(env["WINEDLLOVERRIDES"] == "d3d11=n;dxgi=n;version=n,b;winmm=n,b");
  Env explicit_env{{"WINEDLLOVERRIDES", "*version=b"}};
  configure_mod_loaders(explicit_env, dir / "Game.exe");
  CHECK(explicit_env["WINEDLLOVERRIDES"] == "*version=b;winmm=n,b");
  Env all{{"WINEDLLOVERRIDES", "*=b"}};
  configure_mod_loaders(all, dir / "Game.exe");
  CHECK(all["WINEDLLOVERRIDES"] == "*=b");
  fs::remove_all(dir);
}

TEST_CASE("runtime: backend selection follows install capabilities") {
  CHECK(select_backend("", {}, {}) == "openxr");
  CHECK(select_backend("", {}, {"unity", "unreal"}) == "openxr");
  CHECK(select_backend("", {}, {"d3d12"}) == "openvr");
  CHECK(select_backend("", {}, {"unity-oculus-plugin"}) == "openvr");
  CHECK(select_backend("", {"-VR_Presentation"}, {}) == "openvr");
  CHECK(select_backend(" OpenXR ", {}, {"openvr"}) == "openxr");
  CHECK_THROWS(select_backend("steamvr", {}, {}));
}

TEST_CASE("runtime: engine arguments select the Oculus mode") {
  CHECK(oculus_launch_arguments(Engine::unreal, {"-OpenXR", "-windowed"}) ==
        std::vector<std::string>{"-windowed", "-vr", "-oculus"});
  CHECK(oculus_launch_arguments(Engine::unreal, {"-VR", "-Oculus"}) == std::vector<std::string>{"-VR", "-Oculus"});
  CHECK(oculus_launch_arguments(Engine::unity, {"-vrmode", "OpenVR", "-x"}) ==
        std::vector<std::string>{"-x", "-vrmode", "Oculus"});
  CHECK(oculus_launch_arguments(Engine::other, {"-a"}) == std::vector<std::string>{"-a"});
}

TEST_CASE("runtime: launcher command line") {
  CHECK(launcher_arguments("/t/RiftLiftLauncher.exe", "openvr", "app", "Z:\\g", "Z:\\g\\a.exe", {"-x"}) ==
        std::vector<std::string>{"/t/RiftLiftLauncher.exe", "/wait", "/openvr", "/app", "app", "/cwd", "Z:\\g",
                                 "Z:\\g\\a.exe", "-x"});
}

TEST_CASE("runtime: direct OpenVR runtimes disable wineopenxr, XRizer keeps it") {
  Env env{{"XR_RUNTIME_JSON", "/x.json"}, {"OXR_ZERO_TIME_IS_NOW", "1"}, {"WINEDLLOVERRIDES", "d3d11=n;"}};
  auto xrizer = env;
  disable_openxr_for_direct_openvr(xrizer, "xrizer");
  CHECK(xrizer == env);
  disable_openxr_for_direct_openvr(env, "steamvr");
  CHECK_FALSE(env.contains("XR_RUNTIME_JSON"));
  CHECK_FALSE(env.contains("OXR_ZERO_TIME_IS_NOW"));
  CHECK(env["WINEDLLOVERRIDES"] == "wineopenxr=d;d3d11=n");
}

TEST_CASE("runtime: Proton identity") {
  Env env;
  configure_proton_identity(env, "steam", 1234);
  CHECK(env["SteamAppId"] == "1234");
  CHECK(env["UMU_ID"] == "umu-1234");
  CHECK(env["UMU_USE_STEAM"] == "0");
  Env meta;
  configure_proton_identity(meta, "meta", 1234);
  CHECK(meta["UMU_ID"] == "umu-default");
  CHECK_FALSE(meta.contains("SteamAppId"));
}

TEST_CASE("runtime: Proton environment") {
  Env base{{"VR_OVERRIDE", "/x"}, {"HOME", "/h"}, {"DXVK_LOG_LEVEL", "warn"}};
  ProtonSettings settings;
  settings.prefix = "/data/compatdata";
  settings.steam_root = "/steam";
  settings.install_path = "/game";
  auto env = proton_environment(base, settings);
  CHECK_FALSE(env.contains("VR_OVERRIDE"));
  CHECK(env["HOME"] == "/h");
  CHECK(env["STEAM_COMPAT_DATA_PATH"] == "/data/compatdata");
  CHECK(env["STEAM_COMPAT_LIBRARY_PATHS"] == (fs::path("/steam") / "steamapps").string());
  CHECK(env["PROTON_LOG"] == "0");
  CHECK(env["WINEDEBUG"] == "-all");
  CHECK_FALSE(env.contains("PROTON_LOG_DIR"));
  settings.debug = true;
  settings.proton_logs = "/logs/proton";
  auto debug = proton_environment(base, settings);
  CHECK(debug["PROTON_LOG"] == "1");
  CHECK(debug["WINEDEBUG"] == std::string(debug_wine_channels));
  CHECK(debug["DXVK_LOG_LEVEL"] == "warn");
  CHECK(debug["RUST_LOG"] == "info,xrizer_tracking=debug");
  CHECK(debug["PROTON_LOG_DIR"] == "/logs/proton");
  base["RIFTLIFT_WINEDEBUG"] = "+loaddll";
  CHECK(proton_environment(base, settings)["WINEDEBUG"] == "+loaddll");
}

TEST_CASE("runtime: OpenXR launch environment") {
  Env env{{"WINEDLLOVERRIDES", ";version=n;"}};
  apply_openxr_environment(env, "/r/active_runtime.json");
  CHECK(env["WINEDLLOVERRIDES"] == "d3d11=n;dxgi=n;version=n");
  CHECK(env["RIFTLIFT_OVR_COMPAT"] == "1");
  CHECK(env["PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES"] == "1");
  Env bare;
  apply_openxr_environment(bare, "/r.json");
  CHECK(bare["WINEDLLOVERRIDES"] == "d3d11=n;dxgi=n");
}

TEST_CASE("runtime: OpenVR launch environment") {
  Env env{{"XRIZER_LOG_DIR", "/l"}, {"RIFTLIFT_XRIZER", "1"}};
  configure_openvr_environment(env, "/cfg", "/rt", "/steamvr", "/cfg/openvr/openvrpaths.vrpath", "steamvr");
  CHECK(env["VR_OVERRIDE"] == "/steamvr");
  CHECK(env["XDG_CONFIG_HOME"] == "/cfg");
  CHECK_FALSE(env.contains("XRIZER_LOG_DIR"));
  CHECK_FALSE(env.contains("RIFTLIFT_XRIZER"));
#ifndef _WIN32
  // Wine's view of a Linux path (Proton only).
  CHECK(env["RIFTLIFT_ACTION_MANIFEST"].starts_with("Z:\\"));
#endif
  Env xrizer;
  configure_openvr_environment(xrizer, "/cfg", "/rt", "/x", "/r", "xrizer");
  CHECK(xrizer["RIFTLIFT_XRIZER"] == "1");
  CHECK(xrizer["RIFTLIFT_ACTION_MANIFEST"] == (fs::path("/rt") / "Input/action_manifest.json").string());
}

TEST_CASE("runtime: Flatpak runtimes") {
  auto parts = flatpak_app_parts("/var/lib/flatpak/app/io.github.wivrn.wivrn/x86_64/stable/active/files/x.json");
  REQUIRE(parts);
  CHECK(parts->first == fs::path("/var/lib/flatpak/app/io.github.wivrn.wivrn"));
  CHECK(parts->second == "io.github.wivrn.wivrn");
  CHECK_FALSE(flatpak_app_parts("/var/lib/flatpak/app"));
  CHECK_FALSE(flatpak_app_parts("/usr/share/openxr/1/openxr_monado.json"));
}

TEST_CASE("runtime: WiVRn pressure-vessel advice is validated by shape") {
  auto yes = [](const fs::path &) { return true; };
  auto no = [](const fs::path &) { return false; };
  auto line = "info: For Steam games, set command to PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES=1 "
              "PRESSURE_VESSEL_FILESYSTEMS_RW=/var/lib/flatpak/app/io.github.wivrn.wivrn %command%";
  auto pairs = parse_pressure_vessel_line(line, yes);
  REQUIRE(pairs);
  CHECK(pairs->size() == 2);
  CHECK(pairs->at("PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES") == "1");
  CHECK_FALSE(parse_pressure_vessel_line(line, no));
  CHECK_FALSE(parse_pressure_vessel_line("set command to LD_PRELOAD=/x %command%", yes));
  CHECK_FALSE(parse_pressure_vessel_line("set command to PRESSURE_VESSEL_X=$(rm) %command%", yes));
  CHECK_FALSE(parse_pressure_vessel_line("set command to %command%", yes));
  CHECK_FALSE(parse_pressure_vessel_line("unrelated", yes));
}

TEST_CASE("runtime: OpenXR layer registration is read from system.reg") {
  const std::string key = "Software\\Khronos\\OpenXR\\1\\ApiLayers\\Implicit";
  const std::string name = "Z:\\data\\openxr-layer.json";
  std::string reg = "WINE REGISTRY Version 2\n\n"
                    "[Software\\\\Khronos\\\\OpenXR\\\\1\\\\ApiLayers\\\\Implicit] 1700000000\n"
                    "#time=1\n"
                    "\"Z:\\\\data\\\\openxr-layer.json\"=dword:00000000\n\n"
                    "[Other]\n";
  CHECK(layer_registered(reg, key, name));
  CHECK_FALSE(layer_registered(reg, key, "Z:\\other.json"));
  CHECK_FALSE(layer_registered("", key, name));
}

TEST_CASE("runtime: shell splitting matches shlex") {
  CHECK(shell_split("gamemoderun") == std::vector<std::string>{"gamemoderun"});
  CHECK(shell_split("  mangohud --dlsym  ") == std::vector<std::string>{"mangohud", "--dlsym"});
  CHECK(shell_split("a 'b c' \"d \\\" e\" f\\ g ''") ==
        std::vector<std::string>{"a", "b c", "d \" e", "f g", ""});
  CHECK(shell_split("").empty());
  CHECK_THROWS(shell_split("'open"));
}

TEST_CASE("runtime: platform user IDs") {
  CHECK(validate_user_id(" 0042\n", "id") == "42");
  CHECK(validate_user_id("18446744073709551615", "id") == "18446744073709551615");
  CHECK_THROWS(validate_user_id("0", "id"));
  CHECK_THROWS(validate_user_id("18446744073709551616", "id"));
  CHECK_THROWS(validate_user_id("-5", "id"));
  CHECK_THROWS(validate_user_id("abc", "id"));
}
