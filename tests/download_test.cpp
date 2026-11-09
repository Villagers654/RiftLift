#include "common/download/manager.h"
#include "common/meta/store.h"
#include "tests/temp_dir.h"
#include <doctest/doctest.h>
#include "common/digest.h"
#include "common/paths.h"
#include <atomic>
#include <chrono>
#include <miniz.h>
#include <set>
#include <thread>

using namespace riftlift;

namespace {
// Meta's CDN for one game with two builds, newest first: 111 (version code 2) and 222 (1).
constexpr std::string_view kApp = "1234567890";
const std::string kFile = "Game.exe contents";

std::string zip_json(const Json &m) {
  const auto text = m.dump();
  mz_zip_archive zip{};
  REQUIRE(mz_zip_writer_init_heap(&zip, 0, 0));
  REQUIRE(mz_zip_writer_add_mem(&zip, "manifest.json", text.data(), text.size(), MZ_DEFAULT_COMPRESSION));
  void *bytes = nullptr;
  size_t size = 0;
  REQUIRE(mz_zip_writer_finalize_heap_archive(&zip, &bytes, &size));
  std::string out(static_cast<const char *>(bytes), size);
  mz_zip_writer_end(&zip);
  return out;
}
std::string deflate(const std::string &text) {
  mz_ulong size = mz_compressBound(mz_ulong(text.size()));
  std::string out(size, '\0');
  REQUIRE(mz_compress(reinterpret_cast<unsigned char *>(out.data()), &size,
                      reinterpret_cast<const unsigned char *>(text.data()), mz_ulong(text.size())) == MZ_OK);
  out.resize(size);
  return out;
}
std::string zip_manifest(const std::string &sha) {
  return zip_json({{"canonicalName", "game"},
                   {"launchFile", "Game.exe"},
                   {"files", {{"Game.exe", {{"sha256", sha}, {"size", kFile.size()}, {"segments", Json::array({Json::array({0, sha})})}}}}}});
}
bool has(std::string_view text, std::string_view part) { return text.find(part) != std::string_view::npos; }

struct Cdn {
  std::set<std::string> gone;  // builds whose segments answer 410
  std::string sha = digest::sha256(std::span(reinterpret_cast<const std::uint8_t *>(kFile.data()), kFile.size()));
  download::Manager::Services services(std::atomic<int> *installs, std::string *installed) {
    download::Manager::Services s;
    s.query = [](std::string_view, const http::Fields &) {
      Json binaries = Json::array();
      for (auto [id, code] : {std::pair{"111", "2"}, std::pair{"222", "1"}})
        binaries.push_back({{"__typename", "PCBinary"}, {"id", id}, {"version", std::string("1.") + code}, {"versionCode", code}});
      return Json{{"data", {{"node", {{"displayName", "Game"}, {"supportedBinaries", {{"nodes", binaries}}}}}}}};
    };
    s.read = [this](std::string_view url, const http::Fields &) {
      for (const std::string id : {"111", "222"}) {
        if (has(url, "binaries/download/") && has(url, "id=" + id))
          return http::Response{200, zip_manifest(sha), {}};
        if (has(url, "binaries/segment/") && has(url, "binary_id=" + id))
          return gone.contains(id) ? http::Response{410, R"({"error":{"message":"Gone","code":1}})", {}}
                                   : http::Response{200, deflate(kFile), {}};
      }
      return http::Response{404, {}, {}};
    };
    s.installed = [installs, installed](const download::Installed &done) {
      *installed = done.plan.binary_id;
      ++*installs;
    };
    s.workers = 1;
    return s;
  }
};
download::Job wait_done(download::Manager &manager) {
  for (int i = 0; i < 500; ++i) {
    auto jobs = manager.snapshot();
    if (!jobs.empty() && (jobs[0].state == download::State::complete || jobs[0].state == download::State::failed))
      return jobs[0];
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  FAIL("The download never finished.");
  return {};
}
} // namespace

TEST_CASE("downloads: a build Meta dropped (HTTP 410) falls back to the next one it offers") {
  test::TempDir data("download-gone"), games("download-games");
  Cdn cdn;
  cdn.gone = {"111"};
  std::atomic<int> installs = 0;
  std::string installed;
  download::Manager manager(data.path, [] {}, cdn.services(&installs, &installed), false);
  manager.set_account("0123456789ab");
  manager.enqueue(std::string(kApp), "Game", games.path, "token", "0123456789ab");
  auto job = wait_done(manager);
  CHECK(job.state == download::State::complete);
  CHECK(installed == "222");
  CHECK(installs == 1);
}

TEST_CASE("downloads: when every build is gone, the error says so with Meta's message logged") {
  test::TempDir data("download-gone-all"), games("download-games-all");
  Cdn cdn;
  cdn.gone = {"111", "222"};
  std::atomic<int> installs = 0;
  std::string installed;
  download::Manager manager(data.path, [] {}, cdn.services(&installs, &installed), false);
  manager.set_account("0123456789ab");
  manager.enqueue(std::string(kApp), "Game", games.path, "token", "0123456789ab");
  auto job = wait_done(manager);
  CHECK(job.state == download::State::failed);
  CHECK(has(job.error, "no longer serves the files of any build"));
  CHECK(installs == 0);
}

TEST_CASE("downloads: a chosen version that is gone does not become another") {
  test::TempDir data("download-gone-pinned"), games("download-games-pinned");
  Cdn cdn;
  cdn.gone = {"111"};
  std::atomic<int> installs = 0;
  std::string installed;
  download::Manager manager(data.path, [] {}, cdn.services(&installs, &installed), false);
  manager.set_account("0123456789ab");
  manager.enqueue_version(std::string(kApp), "111", "game-1-2", "Game (1.2)", games.path, "token", "0123456789ab");
  auto job = wait_done(manager);
  CHECK(job.state == download::State::failed);
  CHECK(has(job.error, "no longer serves segment"));
  CHECK(has(job.error, "HTTP 410: Gone"));
  CHECK(installs == 0);
}

TEST_CASE("downloads: segments wait in the game's folder, and one an earlier version cached in app data is used") {
  test::TempDir data("download-cache"), games("download-games-cache");
  Cdn cdn;
  std::atomic<int> installs = 0, fetched = 0;
  std::string installed;
  fs::path dir;
  auto services = cdn.services(&installs, &installed);
  services.read = [read = services.read, &fetched](std::string_view url, const http::Fields &fields) {
    if (has(url, "binaries/segment/"))
      ++fetched;
    return read(url, fields);
  };
  services.installed = [&](const download::Installed &done) {
    dir = done.directory;
    ++installs;
  };
  // 1.0 kept segments here; a download it left unfinished resumes from them.
  fs::create_directories(data.path / "cache" / "segments");
  paths::atomic_write(data.path / "cache" / "segments" / cdn.sha, kFile);
  Json job{{"appId", kApp}, {"name", "Game"}, {"account", "0123456789ab"}, {"root", paths::utf8(games.path)}};
  paths::atomic_write(data.path / "downloads.json", Json{{"version", 3}, {"jobs", {job}}}.dump());
  download::Manager manager(data.path, [] {}, services, true);
  manager.set_account("0123456789ab");
  manager.enqueue(std::string(kApp), "Game", games.path, "token", "0123456789ab");
  auto done = wait_done(manager);
  REQUIRE(done.state == download::State::complete);
  CHECK(fetched == 0);
  CHECK(paths::read(dir / "Game.exe") == kFile);
  CHECK(paths::within(games.path, dir));
  CHECK_FALSE(fs::exists(dir / ".riftlift-segments"));
  CHECK_FALSE(fs::exists(data.path / "cache" / "segments"));
}

TEST_CASE("downloads: a segment two files share stays until the second is assembled") {
  test::TempDir data("download-shared"), games("download-games-shared");
  auto sha = [](const std::string &text) {
    return digest::sha256(std::span(reinterpret_cast<const std::uint8_t *>(text.data()), text.size()));
  };
  const std::string one = "first part ", two = "shared part";
  Json files{{"A.bin", {{"sha256", sha(one + two)}, {"size", (one + two).size()},
                        {"segments", Json::array({Json::array({0, sha(one)}), Json::array({one.size(), sha(two)})})}}},
             {"B.bin", {{"sha256", sha(two)}, {"size", two.size()}, {"segments", Json::array({Json::array({0, sha(two)})})}}}};
  Cdn cdn;
  std::atomic<int> installs = 0;
  std::string installed;
  fs::path dir;
  auto services = cdn.services(&installs, &installed);
  services.read = [&](std::string_view url, const http::Fields &) {
    if (has(url, "binaries/download/"))
      return http::Response{200, zip_json({{"canonicalName", "game"}, {"launchFile", "A.bin"}, {"files", files}}), {}};
    if (has(url, "binaries/segment/"))
      return http::Response{200, deflate(has(url, sha(one)) ? one : two), {}};
    return http::Response{404, {}, {}};
  };
  services.installed = [&](const download::Installed &done) { dir = done.directory; };
  download::Manager manager(data.path, [] {}, services, false);
  manager.set_account("0123456789ab");
  manager.enqueue(std::string(kApp), "Game", games.path, "token", "0123456789ab");
  auto job = wait_done(manager);
  REQUIRE(job.state == download::State::complete);
  CHECK(paths::read(dir / "A.bin") == one + two);
  CHECK(paths::read(dir / "B.bin") == two);
  CHECK_FALSE(fs::exists(dir / ".riftlift-segments"));
}

TEST_CASE("downloads: an app data segment cache with no unfinished download behind it is emptied") {
  test::TempDir data("download-old-cache");
  fs::create_directories(data.path / "cache" / "segments");
  paths::atomic_write(data.path / "cache" / "segments" / "stale", "x");
  Cdn cdn;
  std::atomic<int> installs = 0;
  std::string installed;
  download::Manager manager(data.path, [] {}, cdn.services(&installs, &installed), false);
  CHECK_FALSE(fs::exists(data.path / "cache" / "segments"));
}

TEST_CASE("cdn messages: Meta's words, one line, no tokens") {
  CHECK(meta::cdn_message(R"({"error":{"message":"Gone\nfor good"}})") == "Gone for good");
  CHECK(meta::cdn_message("plain words ") == "plain words");
  CHECK(meta::cdn_message("<html><body>Gone</body></html>").empty());
  CHECK(meta::cdn_message("bad access_token=OC|123456789012|abcdefabcdef") == "bad [token]");
  CHECK(meta::cdn_message(std::string(300, 'x')).size() == 160);
  CHECK(meta::cdn_message("").empty());
}
