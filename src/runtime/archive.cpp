#include "runtime/archive.h"
#include "common/process.h"
#include "runtime/child.h"
#include <algorithm>
#include <cstdio>
#include <fstream>
#include <miniz.h>
#include <stdexcept>

namespace riftlift::runtime {
bool archive_member_safe(std::string_view name) {
  std::string text(name);
  std::ranges::replace(text, '\\', '/');
  if (text.empty() || text.starts_with('/') || text.find(':') != text.npos)
    return false;
  auto normal = fs::path(text).lexically_normal().generic_string();
  return normal != ".." && !normal.starts_with("../");
}
namespace {
struct Zip {
  mz_zip_archive archive{};
  FILE *file = nullptr;
  explicit Zip(const fs::path &path) {
#ifdef _WIN32
    file = _wfopen(path.c_str(), L"rb");
#else
    file = std::fopen(path.c_str(), "rb");
#endif
    if (!file || !mz_zip_reader_init_cfile(&archive, file, 0, 0)) {
      if (file)
        std::fclose(file);
      file = nullptr;
      throw std::runtime_error(paths::utf8(path.filename()) + " is not a readable zip archive.");
    }
  }
  ~Zip() {
    mz_zip_reader_end(&archive);
    std::fclose(file);
  }
  std::string name(mz_uint index) {
    mz_zip_archive_file_stat stat;
    if (!mz_zip_reader_file_stat(&archive, index, &stat))
      throw std::runtime_error("Unreadable zip member.");
    return stat.m_filename;
  }
};
size_t write_stream(void *opaque, mz_uint64, const void *data, size_t size) {
  auto &out = *static_cast<std::ofstream *>(opaque);
  out.write(static_cast<const char *>(data), std::streamsize(size));
  return out ? size : 0;
}
} // namespace
void extract_zip(const fs::path &archive, const fs::path &destination) {
  fs::create_directories(destination);
  Zip zip(archive);
  const auto count = mz_zip_reader_get_num_files(&zip.archive);
  // Check every name before writing anything.
  for (mz_uint i = 0; i < count; ++i)
    if (auto name = zip.name(i); !archive_member_safe(name))
      throw std::runtime_error("unsafe path in " + paths::utf8(archive.filename()) + ": " + name);
  for (mz_uint i = 0; i < count; ++i) {
    auto name = zip.name(i);
    std::ranges::replace(name, '\\', '/');
    auto target = destination / paths::from_utf8(name);
    if (mz_zip_reader_is_file_a_directory(&zip.archive, i)) {
      fs::create_directories(target);
      continue;
    }
    fs::create_directories(target.parent_path());
    std::ofstream out(paths::io_path(target), std::ios::binary | std::ios::trunc);
    if (!out || !mz_zip_reader_extract_to_callback(&zip.archive, i, write_stream, &out, 0))
      throw std::runtime_error("Could not extract " + name + " from " +
                               paths::utf8(archive.filename()) + ".");
  }
}
std::string zip_entry(const fs::path &archive, std::string_view name) {
  Zip zip(archive);
  const int index = mz_zip_reader_locate_file(&zip.archive, std::string(name).c_str(), nullptr, 0);
  size_t size = 0;
  void *data = index < 0 ? nullptr : mz_zip_reader_extract_to_heap(&zip.archive, mz_uint(index), &size, 0);
  if (!data)
    throw std::runtime_error(std::string(name) + " is missing from " + paths::utf8(archive.filename()) + ".");
  std::string bytes(static_cast<const char *>(data), size);
  mz_free(data);
  return bytes;
}

#ifndef _WIN32
std::string tar_member_types(std::string_view listing) {
  std::string types;
  while (!listing.empty()) {
    auto end = listing.find('\n');
    auto line = listing.substr(0, end);
    listing = end == listing.npos ? std::string_view{} : listing.substr(end + 1);
    if (!line.empty())
      types += line.front();
  }
  return types;
}
namespace {
std::string tar_output(const fs::path &tar, const std::vector<std::string> &args,
                       std::stop_token stop) {
  std::string output, error;
  const int code = run_tool(tar, args, [&](std::string_view text) { output += text; }, &error, stop);
  if (stop.stop_requested())
    throw std::runtime_error("Stopped while unpacking.");
  if (code)
    throw std::runtime_error("tar failed: " + error.substr(0, 500));
  return output;
}
} // namespace
void extract_tar(const fs::path &archive, const fs::path &destination, bool allow_links,
                 std::stop_token stop) {
  const auto tar = find_program("tar", current_environment());
  if (tar.empty())
    throw std::runtime_error("required command is missing: tar");
  fs::create_directories(destination);
  const auto file = paths::utf8(archive);
  const auto label = paths::utf8(archive.filename());
  // GNU tar escapes control characters in names, so each member is one line.
  const auto names = tar_output(tar, {"-tf", file}, stop);
  const auto types = tar_member_types(tar_output(tar, {"-tvf", file}, stop));
  std::string_view rest = names;
  size_t index = 0;
  while (!rest.empty()) {
    auto end = rest.find('\n');
    auto name = rest.substr(0, end);
    rest = end == rest.npos ? std::string_view{} : rest.substr(end + 1);
    if (name.empty())
      continue;
    if (index >= types.size())
      throw std::runtime_error("unreadable listing of " + label);
    const char type = types[index++];
    if (!archive_member_safe(name))
      throw std::runtime_error("unsafe path in " + label + ": " + std::string(name));
    if (type == 'l' || type == 'h') {
      if (!allow_links)
        throw std::runtime_error("links are not allowed in " + label + ": " + std::string(name));
    } else if (type != '-' && type != 'd') {
      throw std::runtime_error("unsafe entry in " + label + ": " + std::string(name));
    }
  }
  tar_output(tar, {"-xf", file, "-C", paths::utf8(destination), "--no-same-owner",
                   "--no-same-permissions"}, stop);
}
#endif
} // namespace riftlift::runtime
