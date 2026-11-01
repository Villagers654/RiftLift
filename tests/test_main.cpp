#define DOCTEST_CONFIG_IMPLEMENT
#include "tests/temp_dir.h"
#include <doctest/doctest.h>
#include <cstdlib>

int main(int argc, char **argv) {
  // Tests never touch the user's RiftLift data or Steam profile.
  const auto data = riftlift::test::TempDir("data");
#ifdef _WIN32
  _putenv_s("RIFTLIFT_DATA_DIR", riftlift::paths::utf8(data.path).c_str());
#else
  setenv("RIFTLIFT_DATA_DIR", data.path.c_str(), 1);
#endif
  doctest::Context context(argc, argv);
  return context.run();
}
