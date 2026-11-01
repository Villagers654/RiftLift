#include "runtime/files.h"
#include "common/digest.h"
#include "common/file_ops.h"
#include "common/http/client.h"
#include "common/log.h"
#include "runtime/child.h"
#include <fstream>
#include <random>
#include <thread>

namespace riftlift::runtime {
namespace {
std::string random_suffix() {
  static thread_local std::mt19937_64 engine{std::random_device{}()};
  return std::format("{:016x}", engine());
}
} // namespace
std::string sha256_file(const fs::path &file) {
  std::ifstream in(paths::io_path(file), std::ios::binary);
  if (!in)
    throw std::runtime_error("Could not read " + paths::utf8(file) + ".");
  return digest::sha256(in);
}
std::string try_sha256_file(const fs::path &file) {
  try {
    return sha256_file(file);
  } catch (...) {
    return {};
  }
}
std::string read_file(const fs::path &file) {
  std::ifstream in(paths::io_path(file), std::ios::binary);
  if (!in)
    throw std::runtime_error("Could not read " + paths::utf8(file) + ".");
  return {std::istreambuf_iterator<char>(in), {}};
}
void write_file(const fs::path &target, std::string_view bytes, fs::perms mode) {
  fs::create_directories(target.parent_path());
  auto temporary = target.parent_path() /
                   paths::from_utf8("." + paths::utf8(target.filename()) + "-" + random_suffix());
  try {
    {
      std::ofstream out(paths::io_path(temporary), std::ios::binary | std::ios::trunc);
      out.write(bytes.data(), std::streamsize(bytes.size()));
      out.flush();
      if (!out)
        throw std::runtime_error("Could not write " + paths::utf8(target) +
                                 ". Check free space and folder permissions.");
    }
#ifndef _WIN32
    fs::permissions(temporary, mode, fs::perm_options::replace);
#else
    (void)mode;
#endif
    flush_file(temporary);
    paths::replace_file(temporary, target);
  } catch (...) {
    std::error_code ec;
    fs::remove(temporary, ec);
    throw;
  }
}
void copy_preserving(const fs::path &source, const fs::path &target) {
  fs::copy_file(source, target, fs::copy_options::overwrite_existing);
  std::error_code ec;
  fs::last_write_time(target, fs::last_write_time(source, ec), ec);
}
Staging::Staging(const fs::path &parent, std::string_view prefix) {
  fs::create_directories(parent);
  for (int attempt = 0;; ++attempt) {
    path_ = parent / paths::from_utf8(std::string(prefix) + random_suffix());
    if (fs::create_directory(path_))
      return;
    if (attempt > 8)
      throw std::runtime_error("Could not create a staging folder in " + paths::utf8(parent) + ".");
  }
}
Staging::~Staging() {
  std::error_code ec;
  fs::remove_all(path_, ec);
}
void replace_directory(const fs::path &source, const fs::path &destination) {
  auto backup = destination.parent_path() /
                paths::from_utf8("." + paths::utf8(destination.filename()) + ".riftlift-previous");
  std::error_code ec;
  if (fs::exists(fs::symlink_status(backup)))
    fs::remove_all(backup);
  if (fs::exists(fs::symlink_status(destination)))
    fs::rename(destination, backup);
  try {
    fs::rename(source, destination);
  } catch (...) {
    if (fs::exists(fs::symlink_status(backup, ec)))
      fs::rename(backup, destination, ec);
    throw;
  }
  fs::remove_all(backup, ec);
}
fs::path fetch(const std::string &url, const fs::path &target, std::string_view sha256,
               Origin origin, std::stop_token stop) {
  fs::create_directories(target.parent_path());
  std::error_code ec;
  if (fs::is_regular_file(target, ec) && (sha256.empty() || try_sha256_file(target) == sha256))
    return target;
  // Downloaded beside the target and published only once verified; an
  // interrupted transfer resumes from its .part file.
  auto pending = target;
  pending += ".unverified";
  const auto name = paths::utf8(target.filename());
  log::info("runtime", "Downloading {}...", name);
  uint64_t reported = 0;
  auto progress = [&](uint64_t done, uint64_t total) {
    if (done >= reported + 64 * 1024 * 1024 || (total && done == total)) {
      reported = done;
      if (total)
        log::info("runtime", "{}: {} of {} MiB", name, done >> 20, total >> 20);
      else
        log::info("runtime", "{}: {} MiB", name, done >> 20);
    }
  };
  for (int attempt = 0;; ++attempt) {
    try {
      if (origin == Origin::meta)
        http::download(url, pending, 0, stop, progress);
      else
        http::download_release(url, pending, 0, stop, progress);
      break;
    } catch (const std::exception &e) {
      if (stop.stop_requested())
        throw std::runtime_error("Stopped while downloading " + name + ".");
      // A missing or refused file does not appear by retrying.
      std::string_view message = e.what();
      if (auto at = message.find("(HTTP 4"); at != message.npos)
        throw std::runtime_error(std::format("could not download {}: the server answered {}", name,
                                             message.substr(at + 1, 8)));
      if (attempt == 3)
        throw std::runtime_error("could not download " + name + " after 4 attempts: " + e.what());
      log::warn("runtime", "Download of {} failed ({}); retrying.", name, e.what());
      if (!sleep_for(std::chrono::seconds(1 << attempt), stop))
        throw std::runtime_error("Stopped while downloading " + name + ".");
    }
  }
  auto actual = sha256_file(pending);
  if (!sha256.empty() && actual != sha256) {
    fs::remove(pending, ec);
    throw std::runtime_error(std::format("checksum mismatch for {}: expected {}, got {}", name,
                                         sha256, actual));
  }
#ifndef _WIN32
  fs::permissions(pending, fs::perms(0644), fs::perm_options::replace, ec);
#endif
  paths::replace_file(pending, target);
  return target;
}
} // namespace riftlift::runtime
