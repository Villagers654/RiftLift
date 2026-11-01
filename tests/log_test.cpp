#include "common/log.h"
#include "tests/temp_dir.h"
#include <doctest/doctest.h>
#include <fstream>
#include <iterator>
#include <string>

using namespace riftlift;
namespace {
struct LogFile {
  test::TempDir dir{"log"};
  fs::path path = dir / "test.log";
  LogFile() { log::set_file(paths::utf8(path)); }
  ~LogFile() { log::set_file(""); }
  std::string text() {
    log::set_file("");
    std::ifstream input(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(input), {}};
  }
};
std::size_t count(const std::string& text, std::string_view needle) {
  std::size_t n = 0;
  for (auto at = text.find(needle); at != text.npos; at = text.find(needle, at + needle.size())) ++n;
  return n;
}
} // namespace
TEST_CASE("log lines never keep a Meta access token a game logs") {
  LogFile file;
  const std::string user = "FRLA" + std::string(40, 'x') + "ZD";
  const std::string app = "OC|5812519008825194|" + std::string(32, 'a');
  log::info("Unity", "Beginning login Token: <redacted> : {} and {}.", user, app);
  log::info("Unity", "FRL support and OC|12 and FRLshort stay as written");
  const auto text = file.text();
  CHECK(count(text, "Token: <redacted> : <redacted-token> and <redacted-token>.\n") == 1);
  CHECK(text.find(user) == std::string::npos);
  CHECK(text.find(app) == std::string::npos);
  CHECK(count(text, "FRL support and OC|12 and FRLshort stay as written\n") == 1);
}
