#pragma once
#include "common/paths.h"
#include <atomic>
#include <chrono>
#include <string>
namespace riftlift::test {
// A fresh folder under the system temp directory, removed with its contents.
struct TempDir {
  fs::path path;
  explicit TempDir(std::string_view name = "test") {
    static std::atomic<int> counter{0};
    auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    path = fs::temp_directory_path() /
           ("riftlift-" + std::string(name) + "-" + std::to_string(stamp) + "-" + std::to_string(counter++));
    fs::create_directories(path);
  }
  ~TempDir() {
    std::error_code ec;
    fs::remove_all(path, ec);
  }
  TempDir(const TempDir &) = delete;
  TempDir &operator=(const TempDir &) = delete;
  fs::path operator/(std::string_view child) const { return path / paths::from_utf8(child); }
};
inline void write(const fs::path &file, std::string_view bytes) {
  fs::create_directories(file.parent_path());
  paths::atomic_write(file, bytes);
}
} // namespace riftlift::test
