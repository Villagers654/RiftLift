#include "launcher/debug_report.h"
#include "launcher/diagnosis.h"
#include "tests/temp_dir.h"
#include <doctest/doctest.h>
#include <fstream>

using namespace riftlift;
using namespace riftlift::launcher;

TEST_CASE("debug report: known causes, most specific first") {
  CHECK_FALSE(diagnose("all fine\n").has_value());
  auto unavailable = diagnose("wine: xrCreateInstance failed: -51\n");
  REQUIRE(unavailable);
  CHECK(unavailable->cause.find("VR runtime was not running") != std::string::npos);
  // A device loss after an API version error: the version error is the cause.
  auto version = diagnose("VK_ERROR_DEVICE_LOST\nXR_ERROR_API_VERSION_UNSUPPORTED\n");
  REQUIRE(version);
  CHECK(version->cause.find("OpenXR version") != std::string::npos);
  auto vr = diagnose("Player.log: Failed to initialize the Oculus VR runtime\n");
  REQUIRE(vr);
  CHECK(vr->cause.find("could not start VR") != std::string::npos);
  CHECK(error_line("Something FAILED here"));
  CHECK(error_line("connection timed out"));
  CHECK_FALSE(error_line("errorless profile, xidle"));
  CHECK(noisy_line("Failed to parse bindings for ViveController"));
}

TEST_CASE("debug report: Proton lines a reader needs first") {
  CHECK(proton_line_priority("0098:warn:debugstr:OutputDebugStringA \"OpenXR session failed\"") == 120);
  CHECK(proton_line_priority("0098:err:seh:EXCEPTION_ACCESS_VIOLATION in game.exe") == 110);
  CHECK(proton_line_priority("warn:debugstr:OutputDebugStringA \"RiftLift: patched 8 executable runtime imports\"") == 90);
  CHECK(proton_line_priority("0098:fixme:d3d:something failed") == 0);
  CHECK(proton_line_priority("0098:trace:seh:dispatch_exception code=40010006 failed") == 0);
  CHECK(proton_line_priority("ordinary line") == 0);
}

TEST_CASE("debug report: a failed run, its cause and its evidence") {
  test::TempDir data("report");
  const auto now = std::chrono::floor<std::chrono::seconds>(std::chrono::system_clock::now());
  test::write(data / "logs/runtime-lone-echo.log",
              std::format("  1.000 1 I [runtime] RiftLift runtime 9.9.9 starting lone-echo (account local) at "
                          "{:%FT%TZ}.\n  2.000 1 I [runtime] Game process started (pid 7).\n"
                          "  9.000 1 I [runtime] Lone Echo exited with code 3.\n",
                          now - std::chrono::minutes(2)));
  test::write(data / "logs/runtime-lone-echo.console.log", "wine: XR_ERROR_FORM_FACTOR_UNAVAILABLE\n");
  test::write(data / "diagnostics/graphics/loneecho_d3d11.log", "info: fine\nerr: DXVK: device creation failed\n");
  auto file = write_debug_report({.data = data.path, .version = "9.9.9", .package = "lone-echo",
                                  .game = "Lone Echo", .private_text = {"PrivateAlias"}});
  std::ifstream in(file, std::ios::binary);
  std::string text{std::istreambuf_iterator<char>(in), {}};
  CHECK(text.find("Game: Lone Echo (lone-echo)") != std::string::npos);
  CHECK(text.find("Result: The game stopped unexpectedly (exit code 3).") != std::string::npos);
  CHECK(text.find("start estimated") == std::string::npos);
  CHECK(text.find("=== Likely cause ===\nYour VR runtime had no headset connected.") != std::string::npos);
  CHECK(text.find("- Turn on Debug logging") != std::string::npos);
  CHECK(text.find("err: DXVK: device creation failed") != std::string::npos);
  CHECK(text.find("info: fine") == std::string::npos);
  CHECK(text.find("=== Checks ===") != std::string::npos);
}

TEST_CASE("debug report: personal details are removed") {
  auto text = redact("user PrivateAlias token OC1234567890123456789012345678901234567890XYZ bearer abcdefghijkl",
                     {"PrivateAlias"});
  CHECK(text.find("PrivateAlias") == std::string::npos);
  CHECK(text.find("OC123") == std::string::npos);
  CHECK(text.find("abcdefghijkl") == std::string::npos);
}
