#define DOCTEST_CONFIG_IMPLEMENT
#include "tests/temp_dir.h"
#include <doctest/doctest.h>
#include <cstdlib>
#include <string_view>

int main(int argc, char **argv) {
  // Tests never touch the user's RiftLift data or Steam profile.
  const auto data = riftlift::test::TempDir("data");
#ifdef _WIN32
  _putenv_s("RIFTLIFT_DATA_DIR", riftlift::paths::utf8(data.path).c_str());
#else
  setenv("RIFTLIFT_DATA_DIR", data.path.c_str(), 1);
  // Nor the desktop keyring: with no session bus, credentials use the sealed
  // file. RIFTLIFT_TEST_KEYRING=1 tests the real Secret Service instead.
  if (const char *keyring = std::getenv("RIFTLIFT_TEST_KEYRING"); !keyring || std::string_view(keyring) != "1")
    setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent/riftlift-test-bus", 1);
#endif
  doctest::Context context(argc, argv);
  return context.run();
}
