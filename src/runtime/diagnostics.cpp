#include "runtime/diagnostics.h"
#include <algorithm>
#include <fstream>

namespace riftlift::runtime {
namespace {
constexpr std::uintmax_t MiB = 1024 * 1024;

void prune(const fs::path &directory, std::string_view suffix, size_t keep, std::uintmax_t max_bytes) {
  std::error_code ec;
  std::vector<std::pair<fs::file_time_type, fs::path>> files;
  for (const auto &entry : fs::directory_iterator(directory, ec))
    if (entry.is_regular_file(ec) && paths::utf8(entry.path().filename()).ends_with(suffix))
      files.emplace_back(entry.last_write_time(ec), entry.path());
  std::ranges::sort(files, std::greater{});
  for (size_t i = 0; i < files.size(); ++i) {
    if (i < keep)
      trim_diagnostic_log(files[i].second, max_bytes);
    else
      fs::remove(files[i].second, ec);
  }
}
fs::path folder(const Layout &layout, std::string_view name, std::string_view suffix, size_t keep,
                std::uintmax_t max_bytes) {
  auto directory = layout.data / "diagnostics" / name;
  fs::create_directories(directory);
  std::error_code ec;
  fs::permissions(directory, fs::perms::owner_all, fs::perm_options::replace, ec);
  prune(directory, suffix, keep, max_bytes);
  return directory;
}
} // namespace

void trim_diagnostic_log(const fs::path &file, std::uintmax_t max_bytes) {
  std::error_code ec;
  const auto size = fs::file_size(file, ec);
  if (ec || size <= max_bytes)
    return;
  std::fstream stream(paths::io_path(file), std::ios::in | std::ios::out | std::ios::binary);
  if (!stream)
    return;
  const std::string marker = "\n[middle diagnostic output truncated]\n";
  std::string result;
  auto read_at = [&](std::uintmax_t offset, std::uintmax_t count) {
    std::string bytes(count, '\0');
    stream.seekg(std::streamoff(offset));
    stream.read(bytes.data(), std::streamsize(count));
    bytes.resize(size_t(stream.gcount()));
    stream.clear();
    return bytes;
  };
  if (max_bytes <= marker.size()) {
    result = read_at(size - max_bytes, max_bytes);
  } else {
    const auto head_size = max_bytes / 4, tail_size = max_bytes - head_size - marker.size();
    auto tail = read_at(size - tail_size, tail_size);
    // Start the tail on a whole line when there is one.
    if (auto newline = tail.find('\n'); newline != tail.npos)
      tail.erase(0, newline + 1);
    result = read_at(0, head_size) + marker + tail;
  }
  stream.close();
  // Truncate in place: writers keep appending to the same file.
  std::ofstream out(paths::io_path(file), std::ios::binary | std::ios::in | std::ios::out);
  out.write(result.data(), std::streamsize(result.size()));
  out.close();
  fs::resize_file(file, result.size(), ec);
}
fs::path prepare_proton_logs(const Layout &layout) {
  return folder(layout, "proton", ".log", 5, 8 * MiB);
}
DebugLogs prepare_debug_logs(const Layout &layout) {
  return {prepare_proton_logs(layout), folder(layout, "graphics", "", 10, 2 * MiB),
          folder(layout, "crashes", "", 5, 6 * MiB), folder(layout, "openvr", "", 8, 4 * MiB),
          folder(layout, "game", "", 8, 4 * MiB)};
}
std::vector<fs::path> runtime_trace_paths(const Layout &layout) {
  std::vector<fs::path> result;
  std::error_code ec;
  for (const auto &user : fs::directory_iterator(layout.prefix / "pfx/drive_c/users", ec))
    for (auto relative : {"Temp/riftlift-runtime-trace.log",
                          "AppData/Local/Temp/riftlift-runtime-trace.log"})
      if (auto trace = user.path() / relative; fs::is_regular_file(trace, ec))
        result.push_back(trace);
  return result;
}
void clear_runtime_traces(const Layout &layout) {
  std::error_code ec;
  for (auto &trace : runtime_trace_paths(layout))
    fs::remove(trace, ec);
}
void trim_runtime_traces(const Layout &layout) {
  for (auto &trace : runtime_trace_paths(layout))
    trim_diagnostic_log(trace, 12 * MiB);
}
} // namespace riftlift::runtime
