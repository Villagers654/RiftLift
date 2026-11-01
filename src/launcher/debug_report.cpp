#include "launcher/debug_report.h"
#include "common/environment.h"
#include "common/http/client.h"
#include "common/json.h"
#include "common/library/library.h"
#include "common/meta/platform.h"
#include "common/playtime.h"
#include "common/process.h"
#include "common/rift/detection.h"
#include "common/rift/game.h"
#include "launcher/diagnosis.h"
#include <algorithm>
#include <cctype>
#include <chrono>
#include <condition_variable>
#include <functional>
#include <mutex>
#include <optional>
#include <ranges>
#include <set>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <format>
#include <fstream>
#include <map>
#include <regex>
#include <thread>
#ifdef _WIN32
#include <windows.h>
#include <tlhelp32.h>
#else
#include <sys/utsname.h>
#include <unistd.h>
extern char **environ;
#endif

namespace riftlift::launcher {
namespace {
// Each log keeps its end, where a failure is. The logs share a budget that
// keeps the whole report under the paste service's 1 MB limit, most important
// first; redaction changes the size by a little at most.
constexpr size_t kRuntimeLog = 448 << 10, kSideLog = 128 << 10, kLauncherLog = 128 << 10;
constexpr size_t kReportBudget = 900'000, kPasteLimit = 1'000'000;
constexpr size_t kKeptReports = 10;
constexpr const char *kPasteDays = "90";

std::string read_file(const fs::path &file, size_t limit = 64 << 10) {
  std::ifstream in(file, std::ios::binary);
  std::string text(limit, '\0');
  in.read(text.data(), std::streamsize(limit));
  text.resize(size_t(in.gcount()));
  return text;
}

std::string local_time(std::chrono::system_clock::time_point t, const char *format = "%Y-%m-%d %H:%M:%S") {
  std::time_t time = std::chrono::system_clock::to_time_t(t);
  std::tm tm{};
#ifdef _WIN32
  localtime_s(&tm, &time);
#else
  localtime_r(&time, &tm);
#endif
  char text[64];
  return std::string(text, std::strftime(text, sizeof text, format, &tm));
}
std::string local_time(fs::file_time_type t) {
  return local_time(std::chrono::system_clock::now() +
                    std::chrono::duration_cast<std::chrono::system_clock::duration>(t - fs::file_time_type::clock::now()));
}

#ifdef _WIN32
std::string narrow(const std::wstring &s) { return paths::utf8(fs::path(s)); }

// A string value; for REG_MULTI_SZ (multi = true) its first string.
std::wstring reg_string(HKEY root, const wchar_t *key, const wchar_t *value, bool multi = false) {
  const DWORD types = multi ? RRF_RT_REG_SZ | RRF_RT_REG_MULTI_SZ : RRF_RT_REG_SZ;
  DWORD size = 0;
  if (RegGetValueW(root, key, value, types, nullptr, nullptr, &size) != ERROR_SUCCESS || size < 2)
    return {};
  std::wstring out(size / 2, L'\0');
  if (RegGetValueW(root, key, value, types, nullptr, out.data(), &size) != ERROR_SUCCESS) return {};
  out.resize(wcsnlen(out.c_str(), out.size()));
  return out;
}

// A Vulkan driver manifest path, flagged when the file is gone (the loader
// then skips that driver without a message).
std::string manifest(const std::string &path) {
  std::error_code ec;
  return fs::exists(paths::from_utf8(path), ec) ? path : path + " (file missing)";
}

std::optional<DWORD> reg_dword(HKEY root, const wchar_t *key, const wchar_t *value) {
  DWORD v = 0, size = sizeof v;
  if (RegGetValueW(root, key, value, RRF_RT_REG_DWORD, nullptr, &v, &size) != ERROR_SUCCESS) return {};
  return v;
}

// The values of a loader key (layer and runtime lists): manifest path and its
// DWORD (0 = enabled for layers).
std::vector<std::pair<std::string, DWORD>> reg_values(HKEY root, const wchar_t *key) {
  std::vector<std::pair<std::string, DWORD>> out;
  HKEY h;
  if (RegOpenKeyExW(root, key, 0, KEY_READ, &h) != ERROR_SUCCESS) return out;
  for (DWORD i = 0;; ++i) {
    wchar_t name[2048];
    DWORD name_size = DWORD(std::size(name)), type = 0, data = 0, data_size = sizeof data;
    LONG r = RegEnumValueW(h, i, name, &name_size, nullptr, &type, reinterpret_cast<BYTE *>(&data), &data_size);
    if (r == ERROR_NO_MORE_ITEMS) break;
    if (r == ERROR_SUCCESS || r == ERROR_MORE_DATA) out.emplace_back(narrow(name), type == REG_DWORD ? data : 0);
  }
  RegCloseKey(h);
  return out;
}

std::vector<std::wstring> reg_subkeys(HKEY root, const wchar_t *key) {
  std::vector<std::wstring> out;
  HKEY h;
  if (RegOpenKeyExW(root, key, 0, KEY_READ, &h) != ERROR_SUCCESS) return out;
  for (DWORD i = 0;; ++i) {
    wchar_t name[256];
    DWORD size = DWORD(std::size(name));
    if (RegEnumKeyExW(h, i, name, &size, nullptr, nullptr, nullptr, nullptr) != ERROR_SUCCESS) break;
    out.emplace_back(name);
  }
  RegCloseKey(h);
  return out;
}

void system_section(std::string &out) {
  const wchar_t *nt = L"SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion";
  std::string build = narrow(reg_string(HKEY_LOCAL_MACHINE, nt, L"CurrentBuildNumber"));
  auto ubr = reg_dword(HKEY_LOCAL_MACHINE, nt, L"UBR");
  out += std::format("OS: Windows {} build {}{} ({})\n", std::atoi(build.c_str()) >= 22000 ? "11" : "10", build,
                     ubr ? std::format(".{}", *ubr) : "",
                     narrow(reg_string(HKEY_LOCAL_MACHINE, nt, L"DisplayVersion")));
  out += std::format("CPU: {} ({} threads)\n",
                     narrow(reg_string(HKEY_LOCAL_MACHINE, L"HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0",
                                       L"ProcessorNameString")),
                     std::thread::hardware_concurrency());
  MEMORYSTATUSEX memory{.dwLength = sizeof(MEMORYSTATUSEX)};
  if (GlobalMemoryStatusEx(&memory))
    out += std::format("RAM: {:.1f} GiB, {:.1f} GiB free\n", memory.ullTotalPhys / 1073741824.0,
                       memory.ullAvailPhys / 1073741824.0);
  const std::wstring display = L"SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}";
  for (const auto &sub : reg_subkeys(HKEY_LOCAL_MACHINE, display.c_str())) {
    std::wstring key = display + L"\\" + sub;
    std::string name = narrow(reg_string(HKEY_LOCAL_MACHINE, key.c_str(), L"DriverDesc"));
    if (name.empty()) continue;
    out += std::format("GPU: {}, driver {}\n", name,
                       narrow(reg_string(HKEY_LOCAL_MACHINE, key.c_str(), L"DriverVersion")));
    // The Vulkan loader finds each adapter's driver here. With none usable it has
    // no window-surface support and ANGLE cannot start (no VK_KHR_surface).
    std::string vulkan = narrow(reg_string(HKEY_LOCAL_MACHINE, key.c_str(), L"VulkanDriverName", true));
    if (!vulkan.empty()) out += std::format("  Vulkan driver: {}\n", manifest(vulkan));
  }
}

void xr_section(std::string &out) {
  const wchar_t *xr = L"SOFTWARE\\Khronos\\OpenXR\\1";
  std::string active = narrow(reg_string(HKEY_LOCAL_MACHINE, xr, L"ActiveRuntime"));
  out += std::format("Active OpenXR runtime: {}\n", active.empty() ? "(none registered)" : active);
  for (auto [root, label] : {std::pair{HKEY_LOCAL_MACHINE, "machine"}, std::pair{HKEY_CURRENT_USER, "user"}}) {
    for (const auto &[name, value] : reg_values(root, L"SOFTWARE\\Khronos\\OpenXR\\1\\AvailableRuntimes"))
      out += std::format("OpenXR runtime ({}): {}{}\n", label, name, value ? " (disabled)" : "");
    for (const auto &[name, value] : reg_values(root, L"SOFTWARE\\Khronos\\OpenXR\\1\\ApiLayers\\Implicit"))
      out += std::format("OpenXR implicit layer ({}): {}{}\n", label, name, value ? " (disabled)" : "");
    for (const auto &[name, value] : reg_values(root, L"SOFTWARE\\Khronos\\Vulkan\\Drivers"))
      out += std::format("Vulkan driver ({}): {}{}\n", label, manifest(name), value ? " (disabled)" : "");
    for (const auto &[name, value] : reg_values(root, L"SOFTWARE\\Khronos\\Vulkan\\ImplicitLayers"))
      out += std::format("Vulkan implicit layer ({}): {}{}\n", label, name, value ? " (disabled)" : "");
  }
}
#else
std::string gpu_names();
std::string vr_inputs();
void xr_services(std::string &out);
std::string first_line_with(const fs::path &file, std::string_view prefix) {
  std::ifstream in(file);
  for (std::string line; std::getline(in, line);)
    if (line.starts_with(prefix)) return line.substr(prefix.size());
  return {};
}

void system_section(std::string &out) {
  std::string os = first_line_with("/etc/os-release", "PRETTY_NAME=");
  std::erase(os, '"');
  utsname u{};
  uname(&u);
  out += std::format("OS: {} (kernel {} {})\n", os, u.release, u.machine);
  const char *desktop = std::getenv("XDG_CURRENT_DESKTOP"), *session = std::getenv("XDG_SESSION_TYPE");
  out += std::format("Desktop: {} ({})\n", desktop ? desktop : "unknown", session ? session : "unknown session");
  std::string cpu = first_line_with("/proc/cpuinfo", "model name\t: ");
  out += std::format("CPU: {} ({} threads)\n", cpu, std::thread::hardware_concurrency());
  long pages = sysconf(_SC_PHYS_PAGES), avail = sysconf(_SC_AVPHYS_PAGES), size = sysconf(_SC_PAGE_SIZE);
  out += std::format("RAM: {:.1f} GiB, {:.1f} GiB free\n", double(pages) * size / 1073741824.0,
                     double(avail) * size / 1073741824.0);
  if (auto gpus = gpu_names(); !gpus.empty()) {
    out += gpus;
  } else {
  std::error_code ec;
  for (const auto &card : fs::directory_iterator("/sys/class/drm", ec)) {
    auto name = card.path().filename().string();
    if (!name.starts_with("card") || name.find('-') != std::string::npos) continue;
    auto device = card.path() / "device";
    std::string vendor = read_file(device / "vendor"), id = read_file(device / "device");
    auto driver = fs::read_symlink(device / "driver", ec).filename().string();
    std::erase(vendor, '\n');
    std::erase(id, '\n');
    out += std::format("GPU: {}:{}, driver {}\n", vendor, id, driver);
  }
  }
  out += std::format("VR input devices: {}\n", vr_inputs());
}

void xr_section(std::string &out) {
  xr_services(out);
  std::vector<fs::path> config;
  if (const char *c = std::getenv("XDG_CONFIG_HOME"); c && *c) config.emplace_back(c);
  else if (const char *h = std::getenv("HOME")) config.push_back(fs::path(h) / ".config");
  config.emplace_back("/etc/xdg");
  config.emplace_back("/etc");
  std::error_code ec;
  for (const auto &dir : config) {
    auto active = dir / "openxr/1/active_runtime.json";
    if (!fs::exists(active, ec)) continue;
    auto target = fs::canonical(active, ec);
    out += std::format("Active OpenXR runtime: {} -> {}\n", paths::utf8(active), paths::utf8(target));
    break;
  }
  std::vector<fs::path> data;
  if (const char *d = std::getenv("XDG_DATA_HOME"); d && *d) data.emplace_back(d);
  else if (const char *h = std::getenv("HOME")) data.push_back(fs::path(h) / ".local/share");
  for (auto d : {"/usr/local/share", "/usr/share", "/etc"}) data.emplace_back(d);
  for (const auto &dir : data)
    for (auto [sub, label] : {std::pair{"openxr/1/api_layers/implicit.d", "OpenXR implicit layer"},
                              std::pair{"vulkan/implicit_layer.d", "Vulkan implicit layer"}})
      for (const auto &f : fs::directory_iterator(dir / sub, ec))
        out += std::format("{}: {}\n", label, paths::utf8(f.path()));
}
#endif

void log_section(std::string &out, const fs::path &file, size_t limit) {
  std::error_code ec;
  auto size = fs::file_size(file, ec);
  if (ec) return;
  constexpr size_t header = 256;
  if (out.size() + header >= kReportBudget) {
    out += std::format("\n=== {} left out: the report is full ===\n", paths::utf8(file.filename()));
    return;
  }
  limit = std::min(limit, kReportBudget - out.size() - header);
  out += std::format("\n=== {} ({} KiB, written {}{}) ===\n", paths::utf8(file.filename()), (size + 1023) / 1024,
                     local_time(fs::last_write_time(file, ec)), size > limit ? ", end only" : "");
  std::string text = tail_log(file, limit);
  out += text;
  if (!text.empty() && text.back() != '\n') out += '\n';
}

// Keeps the newest few reports.
void prune(const fs::path &dir) {
  std::error_code ec;
  std::vector<fs::directory_entry> reports;
  for (const auto &e : fs::directory_iterator(dir, ec))
    if (e.path().extension() == ".txt") reports.push_back(e);
  if (reports.size() <= kKeptReports) return;
  std::ranges::sort(reports, std::greater{}, [](const auto &e) { return e.last_write_time(); });
  for (size_t i = kKeptReports; i < reports.size(); ++i) fs::remove(reports[i].path(), ec);
}

using Clock = std::chrono::system_clock;
Clock::time_point to_system(fs::file_time_type t) {
  return Clock::now() + std::chrono::duration_cast<Clock::duration>(t - fs::file_time_type::clock::now());
}
std::optional<Clock::time_point> modified(const fs::path &file) {
  std::error_code ec;
  auto t = fs::last_write_time(file, ec);
  if (ec) return std::nullopt;
  return to_system(t);
}
std::string duration_text(Clock::duration d) {
  auto seconds = std::chrono::duration_cast<std::chrono::seconds>(d).count();
  if (seconds < 60) return std::format("{}s", seconds);
  if (seconds < 3600) return std::format("{}m {}s", seconds / 60, seconds % 60);
  return std::format("{}h {}m", seconds / 3600, seconds / 60 % 60);
}
std::string one_line(std::string_view text, size_t limit = 600) {
  std::string out(text.substr(0, std::min(text.size(), limit)));
  while (!out.empty() && (out.back() == '\r' || out.back() == '\n' || out.back() == ' ')) out.pop_back();
  size_t start = out.find_first_not_of(' ');
  return start == std::string::npos ? std::string() : out.substr(start);
}
std::vector<std::string> lines_of(std::string_view text) {
  std::vector<std::string> out;
  for (size_t at = 0; at < text.size();) {
    size_t end = text.find('\n', at);
    if (end == std::string::npos) end = text.size();
    out.emplace_back(text.substr(at, end - at));
    at = end + 1;
  }
  return out;
}

// What the runtime log says about the run.
struct Run {
  std::optional<Clock::time_point> started, ended;
  std::string version;  // the runtime that ran it
  std::string result;
  bool failed = false, finished = false;
};
Run read_run(const fs::path &log, const fs::path &console) {
  Run run;
  const std::string text = tail_log(log, 4 << 20);
  run.ended = modified(log);
  if (auto c = modified(console); c && (!run.ended || *c > *run.ended)) run.ended = c;
  constexpr std::string_view starting = "] RiftLift runtime ";
  std::optional<int> exit;
  bool stopped = false;
  for (const auto &line : lines_of(text)) {
    if (auto at = line.find(starting); at != std::string::npos) {
      run = Run{.ended = run.ended};
      exit.reset();
      stopped = false;
      auto rest = line.substr(at + starting.size());
      run.version = rest.substr(0, rest.find(' '));
      // "... (account local) at 2026-11-01T22:20:00Z."
      if (auto when = rest.rfind(" at "); when != std::string::npos) {
        int y, mo, d, h, mi, sec;
        if (std::sscanf(rest.c_str() + when + 4, "%4d-%2d-%2dT%2d:%2d:%2dZ", &y, &mo, &d, &h, &mi, &sec) == 6)
          run.started = std::chrono::sys_days(std::chrono::year(y) / mo / d) + std::chrono::hours(h) +
                        std::chrono::minutes(mi) + std::chrono::seconds(sec);
      }
    }
    if (line.find("Stop requested.") != std::string::npos || line.find("The launcher exited") != std::string::npos)
      stopped = true;
    if (auto at = line.find(" exited with code "); at != std::string::npos && line.find("] ") < at)
      exit = std::atoi(line.c_str() + at + 18);
    if (auto at = line.find("] Exit code: "); at != std::string::npos) exit = std::atoi(line.c_str() + at + 13);
  }
  run.finished = exit.has_value();
  const std::string failure = library::runtime_failure(text, exit.value_or(0));
  const bool runtime_error = !failure.starts_with("The game stopped unexpectedly");
  if (stopped)
    run.result = std::format("stopped from RiftLift{}", exit ? std::format(" (exit code {})", *exit) : "");
  else if (runtime_error) {
    run.result = "did not start: " + failure;
    run.failed = true;
  } else if (!exit) {
    run.result = "no exit recorded (still running, or RiftLift was closed while it ran)";
  } else if (*exit != 0) {
    run.result = failure;
    run.failed = true;
  } else {
    run.result = "the game exited normally (exit code 0)";
  }
  if (int entitlement = library::entitlement_error(log)) {
    run.result += std::format("; Meta answered the game's ownership check with {} ({})", entitlement,
                              meta::platform_error_text(meta::PlatformError(entitlement)));
    run.failed = true;
  }
  return run;
}

// The span system logs are matched against: the run, with a little margin.
struct Window {
  Clock::time_point from, to;
  bool contains(Clock::time_point t) const { return t >= from && t <= to; }
};

#ifndef _WIN32
fs::path which(std::string_view name) {
  const char *path = std::getenv("PATH");
  for (auto dir : std::views::split(std::string_view(path ? path : "/usr/bin:/bin"), ':')) {
    fs::path candidate = fs::path(std::string(dir.begin(), dir.end())) / name;
    if (access(candidate.c_str(), X_OK) == 0) return candidate;
  }
  return {};
}
#endif
// A tool's standard output, or nothing when it is missing, fails to start or
// takes longer than `timeout`.
std::string capture(const fs::path &program, const std::vector<std::string> &args,
                    std::chrono::seconds timeout = std::chrono::seconds(5)) {
  if (program.empty()) return {};
  std::string out;
  std::stop_source stop;
  std::jthread timer([&](std::stop_token done) {
    std::mutex m;
    std::unique_lock lock(m);
    std::condition_variable_any().wait_for(lock, done, timeout, [] { return false; });
    stop.request_stop();
  });
  try {
    run_tool(program, args, [&](std::string_view chunk) {
      if (out.size() < (1 << 20)) out += chunk;
    }, nullptr, stop.get_token());
  } catch (...) {
  }
  return out;
}

// Error lines from a log's end (the last `keep`), else its last lines when
// `tail` is set.
std::vector<std::string> log_errors(const fs::path &file, size_t keep, bool tail) {
  std::vector<std::string> errors, last;
  for (const auto &line : lines_of(tail_log(file, 512 << 10))) {
    auto text = one_line(line);
    if (text.empty() || noisy_line(text)) continue;
    if (error_line(text)) errors.push_back(text);
    last.push_back(text);
  }
  auto &chosen = errors.empty() && tail ? last : errors;
  if (chosen.size() > keep) chosen.erase(chosen.begin(), chosen.end() - std::ptrdiff_t(keep));
  return chosen;
}
// The newest files in `directory` (recursively when `deep`) written during
// the run whose names pass `wanted`.
std::vector<fs::path> files_in_window(const fs::path &directory, const Window &window, size_t keep,
                                      const std::function<bool(const fs::path &)> &wanted, bool deep = false) {
  std::vector<std::pair<Clock::time_point, fs::path>> found;
  std::error_code ec;
  auto consider = [&](const fs::directory_entry &e) {
    if (!e.is_regular_file(ec) || !wanted(e.path())) return;
    if (auto t = modified(e.path()); t && window.contains(*t)) found.emplace_back(*t, e.path());
  };
  if (deep) {
    size_t seen = 0;
    fs::recursive_directory_iterator it(directory, fs::directory_options::skip_permission_denied, ec), end;
    for (; it != end && seen < 20000; it.increment(ec), ++seen) {
      if (ec) break;
      auto name = paths::utf8(it->path().filename());
      std::ranges::transform(name, name.begin(), [](unsigned char c) { return char(std::tolower(c)); });
      if (it->is_directory(ec) && (name == "cache" || name == "gpucache" || name == "shadercache" ||
                                   name == "webcache" || name == "temporary internet files" || it.depth() > 6)) {
        it.disable_recursion_pending();
        continue;
      }
      consider(*it);
    }
  } else {
    for (const auto &e : fs::directory_iterator(directory, ec)) consider(e);
  }
  std::ranges::sort(found, std::greater{});
  std::vector<fs::path> out;
  for (size_t i = 0; i < found.size() && i < keep; ++i) out.push_back(found[i].second);
  return out;
}
void evidence_from(std::string &out, std::string_view label, const fs::path &file,
                   const std::vector<std::string> &lines) {
  if (lines.empty()) return;
  out += std::format("{}{}{}:\n", label, label.empty() ? "" : " ", paths::utf8(file));
  for (const auto &line : lines) out += "  " + line + "\n";
}

// Proton log lines worth reading, highest priority first, at most 12, in log order.
std::vector<std::string> proton_lines(const fs::path &file) {
  std::vector<std::tuple<int, size_t, std::string>> ranked;
  size_t index = 0;
  for (const auto &line : lines_of(tail_log(file, 8 << 20))) {
    ++index;
    auto text = one_line(line);
    if (int priority = proton_line_priority(text)) ranked.emplace_back(priority, index, text);
  }
  std::ranges::sort(ranked, [](auto &a, auto &b) {
    return std::get<0>(a) != std::get<0>(b) ? std::get<0>(a) > std::get<0>(b) : std::get<1>(a) < std::get<1>(b);
  });
  std::vector<std::pair<size_t, std::string>> chosen;
  std::set<std::string> seen;
  const int minimum = ranked.empty() ? 0 : std::max(45, std::get<0>(ranked.front()) - 30);
  static const std::regex prefix(R"(^\d+\.\d+:[0-9a-f]+:[0-9a-f]+:)");
  for (auto &[priority, at, text] : ranked) {
    if (priority < minimum || chosen.size() == 12) break;
    if (seen.insert(std::regex_replace(text, prefix, "")).second) chosen.emplace_back(at, text);
  }
  std::ranges::sort(chosen);
  std::vector<std::string> out;
  for (auto &c : chosen) out.push_back(std::move(c.second));
  return out;
}

// Programs that matter to a VR launch: runtimes, compositors, Wine, Steam.
bool relevant_process(std::string name) {
  std::ranges::transform(name, name.begin(), [](unsigned char c) { return char(std::tolower(c)); });
  for (auto key : {"riftlift", "wine", "proton", "openxr", "xrizer", "wivrn", "monado", "steamvr", "vrserver",
                   "vrcompositor", "vrmonitor", "gamescope", "envision", "alvr", "ovrserver", "ovrservice",
                   "virtualdesktop", "oculusclient", "pressure-vessel"})
    if (name.find(key) != std::string::npos) return true;
  return false;
}
#ifdef _WIN32
void processes_section(std::string &out) {
  std::vector<std::string> found;
  if (HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0); snapshot != INVALID_HANDLE_VALUE) {
    PROCESSENTRY32W entry{.dwSize = sizeof(PROCESSENTRY32W)};
    for (bool more = Process32FirstW(snapshot, &entry); more; more = Process32NextW(snapshot, &entry))
      if (auto name = narrow(entry.szExeFile); relevant_process(name) && entry.th32ProcessID != GetCurrentProcessId())
        found.push_back(std::format("{} {}", entry.th32ProcessID, name));
    CloseHandle(snapshot);
  }
  out += "\n=== VR processes running now ===\n";
  for (const auto &p : found) out += p + "\n";
  if (found.empty()) out += "(none)\n";
}
#else
void processes_section(std::string &out) {
  std::vector<std::string> found;
  std::error_code ec;
  for (const auto &e : fs::directory_iterator("/proc", ec)) {
    auto pid = e.path().filename().string();
    if (pid.empty() || !std::ranges::all_of(pid, [](unsigned char c) { return std::isdigit(c); })) continue;
    std::string name = read_file(e.path() / "comm", 256);
    std::erase(name, '\n');
    if (relevant_process(name) && pid != std::to_string(getpid())) found.push_back(pid + " " + name);
    if (found.size() >= 30) break;
  }
  out += "\n=== VR processes running now ===\n";
  for (const auto &p : found) out += p + "\n";
  if (found.empty()) out += "(none)\n";
}
// GPU names and their kernel drivers, from lspci when it is installed.
std::string gpu_names() {
  std::string out;
  auto lines = lines_of(capture(which("lspci"), {"-nnk"}, std::chrono::seconds(3)));
  for (size_t i = 0; i < lines.size(); ++i) {
    const auto &line = lines[i];
    if (line.find("VGA compatible controller") == std::string::npos && line.find("3D controller") == std::string::npos &&
        line.find("Display controller") == std::string::npos)
      continue;
    std::string driver;
    for (size_t j = i + 1; j < lines.size() && !lines[j].empty() && std::isspace(static_cast<unsigned char>(lines[j][0])); ++j)
      if (auto at = lines[j].find("Kernel driver in use: "); at != std::string::npos) driver = lines[j].substr(at + 22);
    // "03:00.0 VGA compatible controller [0300]: AMD ... [1002:7480]"
    auto name = line.substr(line.find(": ") == std::string::npos ? 0 : line.find(": ") + 2);
    out += std::format("GPU: {}{}\n", name, driver.empty() ? "" : ", driver " + driver);
  }
  return out;
}
// Headsets and controllers the kernel sees.
std::string vr_inputs() {
  std::ifstream in("/proc/bus/input/devices");
  std::set<std::string> names;
  static const std::regex vr(R"((controller|gamepad|sense|oculus|rift|vive|index|quest|vr2|psvr|knuckles|touch|tracker|lighthouse|watchman|pico|bigscreen))",
                             std::regex::icase);
  for (std::string line; std::getline(in, line);)
    if (line.starts_with("N: Name=")) {
      auto name = line.substr(8);
      std::erase(name, '"');
      if (std::regex_search(name, vr)) names.insert(name);
    }
  std::string out;
  for (const auto &n : names) out += (out.empty() ? "" : ", ") + n;
  return out.empty() ? "none detected" : out;
}
void xr_services(std::string &out) {
  const auto systemctl = which("systemctl");
  for (auto service : {"monado.service", "wivrn.service", "wivrn-server.service"}) {
    auto load = one_line(capture(systemctl, {"--user", "show", service, "-p", "LoadState", "--value"}, std::chrono::seconds(2)));
    if (load != "loaded") continue;
    auto state = one_line(capture(systemctl, {"--user", "is-active", service}, std::chrono::seconds(2)));
    out += std::format("{}: {}\n", service, state.empty() ? "unknown" : state);
  }
  // Flatpak installs of the common runtimes.
  std::error_code ec;
  if (const char *home = std::getenv("HOME"))
    for (auto app : {"io.github.wivrn.wivrn", "org.monado.Monado", "org.gabmus.envision"})
      if (fs::is_directory(fs::path(home) / ".var/app" / app, ec)) out += std::format("Flatpak app data: {}\n", app);
}
#endif

// Every log written during the run, cut to its errors: what the doctor
// gathered. Returns the evidence text for the likely-cause rules.
std::string evidence_section(std::string &out, const fs::path &data, const Window &window, bool failed) {
  std::string evidence;
  const auto lower_name = [](const fs::path &p) {
    auto n = paths::utf8(p.filename());
    std::ranges::transform(n, n.begin(), [](unsigned char c) { return char(std::tolower(c)); });
    return n;
  };
  const auto any = [](const fs::path &) { return true; };
  // Game logs: Unity's Player.log, Unreal's Saved/Logs, crash logs.
  const auto game_log = [&](const fs::path &p) {
    auto n = lower_name(p);
    if (n == "player.log" || n == "output_log.txt" || n == "crash.log" || n == "error.log" ||
        n == "riftliftlauncher.txt" || n == "riftlift-runtime-trace.log")
      return true;
    auto parent = lower_name(p.parent_path());
    return parent == "logs" && (n.ends_with(".log") || n.ends_with(".txt"));
  };
  auto add = [&](std::string_view label, const fs::path &file, std::vector<std::string> lines) {
    for (const auto &l : lines) evidence += l + "\n";
    evidence_from(out, label, file, lines);
  };
  const auto diagnostics = data / "diagnostics";
  for (const auto &file : files_in_window(diagnostics / "proton", window, 2,
                                          [&](const fs::path &p) { return lower_name(p).ends_with(".log"); }))
    add("Proton", file, proton_lines(file));
  for (auto [folder, tail] : {std::pair{"graphics", false}, std::pair{"openvr", true}, std::pair{"game", false},
                              std::pair{"crashes", true}})
    for (const auto &file : files_in_window(diagnostics / folder, window, 4, any))
      add("", file, log_errors(file, 8, tail));
#ifdef _WIN32
  std::vector<fs::path> game_roots;
  if (const char *profile = std::getenv("USERPROFILE")) {
    game_roots.push_back(fs::path(paths::from_utf8(profile)) / "AppData/LocalLow");
    std::error_code ec;
    // Unreal games: %LOCALAPPDATA%\<Project>\Saved\Logs.
    for (const auto &e : fs::directory_iterator(fs::path(paths::from_utf8(profile)) / "AppData/Local", ec))
      if (fs::is_directory(e.path() / "Saved/Logs", ec)) game_roots.push_back(e.path() / "Saved/Logs");
  }
#else
  std::vector<fs::path> game_roots{data / "compatdata/pfx/drive_c/users"};
#endif
  std::vector<fs::path> game_logs;
  for (const auto &root : game_roots)
    for (auto &f : files_in_window(root, window, 3, game_log, true)) game_logs.push_back(f);
  std::ranges::sort(game_logs, std::greater{}, [](const fs::path &p) { return modified(p).value_or(Clock::time_point{}); });
  for (size_t i = 0; i < game_logs.size() && i < 3; ++i) add("Game log", game_logs[i], log_errors(game_logs[i], 6, failed));
#ifndef _WIN32
  // Steam's and SteamVR's logs, then the runtimes'.
  if (const char *home = std::getenv("HOME")) {
    const fs::path h(home);
    for (auto root : {".local/share/Steam", ".steam/steam", ".var/app/com.valvesoftware.Steam/data/Steam"}) {
      std::error_code ec;
      if (!fs::is_directory(h / root / "logs", ec)) continue;
      static const std::regex steam_log(R"((vr|openxr|vulkan|console|stderr|connection))", std::regex::icase);
      for (const auto &file : files_in_window(h / root / "logs", window, 6, [&](const fs::path &p) {
             auto n = lower_name(p);
             return (n.ends_with(".txt") || n.ends_with(".log")) && std::regex_search(n, steam_log);
           }))
        add("Steam", file, log_errors(file, 5, false));
      break;
    }
    const char *cache = std::getenv("XDG_CACHE_HOME");
    std::vector<fs::path> runtime_logs{(cache && *cache ? fs::path(cache) : h / ".cache") / "envision/logs",
                                       h / ".local/state/wivrn/wivrn-dashboard",
                                       h / ".var/app/io.github.wivrn.wivrn/.local/state/wivrn/wivrn-dashboard",
                                       h / ".var/app/org.gabmus.envision/cache/envision/logs"};
    for (const auto &dir : runtime_logs)
      for (const auto &file : files_in_window(dir, window, 2, any)) add("VR runtime", file, log_errors(file, 10, failed));
  }
  // The system journal for the run: the user's services, the kernel (GPU
  // resets, out of memory) and crashed programs.
  const auto journalctl = which("journalctl");
  const auto since = local_time(window.from), until = local_time(window.to);
  auto journal = [&](std::string_view title, std::vector<std::string> args) {
    std::vector<std::string> found;
    for (const auto &line : lines_of(capture(journalctl, args)))
      if (auto text = one_line(line); error_line(text) && !noisy_line(text)) found.push_back(text);
    if (found.size() > 10) found.erase(found.begin(), found.end() - 10);
    if (found.empty()) return;
    out += std::format("{}:\n", title);
    for (const auto &f : found) out += "  " + f + "\n", evidence += f + "\n";
  };
  journal("User journal", {"--user", "--since=" + since, "--until=" + until, "--no-pager", "-o", "short-iso", "-n",
                           "300", "--grep=(riftlift|xrizer|openxr|wineopenxr|proton|wivrn|monado|envision|steamvr|"
                           "vrserver|vrcompositor|vulkan|dxvk|vkd3d)"});
  journal("Kernel", {"-k", "--since=" + since, "--until=" + until, "--no-pager", "-o", "short-iso", "-n", "400",
                     "--grep=(amdgpu|nouveau|nvidia|i915|xe |drm|gpu|vulkan|xid|oom)"});
  {
    std::vector<std::string> dumps;
    static const std::regex relevant(R"((riftlift|wine|proton|steam|openxr|xrizer|wivrn|monado|envision|\.exe))",
                                     std::regex::icase);
    for (const auto &line : lines_of(capture(which("coredumpctl"), {"--since=" + since, "--until=" + until,
                                                                    "--no-pager", "--no-legend", "list"})))
      if (line.find("steamwebhelper") == std::string::npos && std::regex_search(line, relevant))
        dumps.push_back(one_line(line));
    if (dumps.size() > 6) dumps.erase(dumps.begin(), dumps.end() - 6);
    if (!dumps.empty()) {
      out += "Crashed programs (coredumpctl):\n";
      for (const auto &d : dumps) out += "  " + d + "\n", evidence += "coredump " + d + "\n";
    }
  }
#endif
  return evidence;
}

// Every game record; engine and runtime markers for the reported game only
// (finding them reads the whole game folder).
void library_section(std::string &out, const fs::path &data, std::string_view package) {
  auto records = rift::load_all(data);
  out += std::format("\n=== Library ({} games{}) ===\n", records.games.size(),
                     records.complete ? "" : ", some records could not be read");
  std::ranges::sort(records.games, {}, &rift::Game::name);
  for (const auto &g : records.games) {
    std::error_code ec;
    const auto executable = g.executable_path();
    const bool present = fs::is_regular_file(executable, ec);
    std::string details = g.source;
    if (!g.app_id.empty()) details += "; app " + g.app_id;
    if (!g.version.empty()) details += "; version " + g.version;
    if (!g.platform_shim) details += "; Platform SDK bridge off";
    if (present && g.slug == package) {
      for (auto [found, name] : {std::pair{rift::uses_oculus_runtime(g.directory), "Oculus SDK"},
                                 std::pair{rift::uses_openvr_runtime(g.directory), "OpenVR"},
                                 std::pair{rift::uses_unity_oculus_plugin(g.directory), "Unity Oculus plugin"},
                                 std::pair{rift::is_unity_player(executable), "Unity"},
                                 std::pair{rift::is_unreal_shipping(executable), "Unreal"},
                                 std::pair{rift::uses_d3d12_runtime(executable), "D3D12"}})
        if (found) details += std::string("; ") + name;
      if (!g.launch_options.empty() || !g.arguments.empty()) details += "; custom arguments";
      if (!g.dll_overrides.empty()) details += "; DLL overrides " + g.dll_overrides;
      for (const auto &[key, value] : g.environment) details += std::format("; {}={}", key, value);
    }
    if (auto played = playtime::get(rift::state_dir(data), g.slug); played.launches)
      details += std::format("; {} launches, {}", played.launches, playtime::format(played.seconds));
    out += std::format("{:<7} {} [{}]{}\n", present ? "OK" : "MISSING", g.name, details,
                       g.slug == package ? " <- this report" : "");
  }
}

void environment_section(std::string &out) {
  std::vector<std::string> lines;
#ifdef _WIN32
  if (wchar_t *block = GetEnvironmentStringsW()) {
    for (wchar_t *p = block; *p; p += wcslen(p) + 1) lines.push_back(narrow(p));
    FreeEnvironmentStringsW(block);
  }
#else
  for (char **p = environ; *p; ++p) lines.emplace_back(*p);
#endif
  std::ranges::sort(lines);
  // RiftLift's own, the XR and graphics stacks', and the overlays and
  // injectors known to break VR games ("NAME=" matches one variable).
  static constexpr std::string_view wanted[] = {
      "RIFTLIFT_", "XR_", "VK_", "OXR_", "STEAMVR_", "PRESSURE_VESSEL_", "PROTON_", "WINE", "DXVK_", "VKD3D_",
      "VR_OVERRIDE=", "VR_PATHREG_OVERRIDE=", "LD_PRELOAD=", "DRI_PRIME=", "MESA_VK_DEVICE_SELECT=",
      "__NV_PRIME_RENDER_OFFLOAD=", "__GLX_VENDOR_LIBRARY_NAME=", "AMD_VULKAN_ICD=", "MANGOHUD",
      "DISABLE_MANGOHUD=", "ENABLE_VKBASALT=", "OBS_VKCAPTURE=", "GAMESCOPE_WSI=", "ENABLE_GAMESCOPE_WSI=",
      "XDG_CURRENT_DESKTOP=", "XDG_SESSION_TYPE=", "WAYLAND_DISPLAY=", "DISPLAY="};
  bool any = false;
  for (const auto &line : lines)
    for (std::string_view prefix : wanted)
      if (line.size() > prefix.size() &&
          std::equal(prefix.begin(), prefix.end(), line.begin(),
                     [](char a, char b) { return a == std::toupper(static_cast<unsigned char>(b)); })) {
        out += line + "\n";
        any = true;
        break;
      }
  if (!any) out += "(none set)\n";
}
} // namespace

int proton_line_priority(std::string_view line) {
  std::string low(line);
  for (char &c : low) c = char(std::tolower(static_cast<unsigned char>(c)));
  auto has = [&](std::string_view s) { return low.find(s) != std::string::npos; };
  if (line.empty() || noisy_line(line) || has("trace:seh:dispatch_exception code=40010006") ||
      has("warn:seh:dispatch_exception \"") || has("warn:module:find_builtin_dll cannot find builtin library"))
    return 0;
  const bool app = has("debugstr:outputdebugstring");
  const bool vr = has("riftlift") || has("openxr") || has("xr_") || has("ovr") || has("oculus") || has("vrclient");
  static const std::regex assertion(R"(\b(_?w?assert|assertion)\b)");
  const bool crash = has("exception_access_violation") || has("unhandled exception") || has("access violation") ||
                     has("segfault") || has("page fault") || has("fatal") || has("panic") || has("crash detected") ||
                     std::regex_search(low, assertion);
  const bool failure = error_line(line);
  static const std::regex bare(R"(outputdebugstring[aw]? "\[error\]\s*")");
  const bool bare_error = app && std::regex_search(low, bare);
  if (app && vr && failure) return 120;
  if (app && crash) return 115;
  if (crash) return 110;
  if (has("riftlift:") && failure) return 105;
  if (vr && failure) return 100;
  if (has("riftlift: patched")) return 90;
  if (app && failure && !bare_error) return 85;
  if (has(":err:") && failure) return 75;
  if (app && failure && bare_error) return 55;
  if (failure && !has(":fixme:")) return 45;
  return 0;
}


std::string redact(std::string text, const std::vector<std::string> &private_text) {
  struct Name {
    std::string text, replacement;
    bool word;  // a name, matched only as a whole word
  };
  std::vector<Name> names;
#ifdef _WIN32
  const char *home = std::getenv("USERPROFILE");
  const char *user = std::getenv("USERNAME");
  const char *host = std::getenv("COMPUTERNAME");
#else
  const char *home = std::getenv("HOME");
  const char *user = std::getenv("USER");
  char host_buffer[256] = {};
  const char *host = gethostname(host_buffer, sizeof host_buffer - 1) == 0 ? host_buffer : nullptr;
#endif
  if (home && *home) {
    std::string h = home;
    names.push_back({h, "~", false});
    std::ranges::replace(h, '\\', '/');
    names.push_back({h, "~", false});
    std::string escaped;  // as JSON writes it
    for (char c : std::string(home)) escaped += c == '\\' ? std::string("\\\\") : std::string(1, c);
    names.push_back({escaped, "~", false});
    // Resolved paths name the real home (rpm-ostree: /var/home/<user> behind
    // /home); it is replaced whole, before the shorter HOME it ends with.
    std::error_code ec;
    if (auto real = paths::utf8(fs::canonical(paths::from_utf8(home), ec)); !ec && real != home) {
      names.push_back({real, "~", false});
      std::ranges::replace(real, '\\', '/');
      names.push_back({real, "~", false});
    }
#ifndef _WIN32
    // A path recorded by another process (the host, outside a container)
    // may name the resolved home even where this one cannot resolve it.
    if (std::string_view(home).starts_with("/home/")) names.push_back({"/var" + std::string(home), "~", false});
#endif
  }
  // Short names would also match ordinary words.
  if (user && std::strlen(user) >= 3) names.push_back({user, "<user>", true});
  // A computer named after its distribution (bazzite, fedora) would also
  // hide the OS line; that name says nothing about the person.
  bool distribution_name = false;
#ifndef _WIN32
  if (host) {
    std::string low_host(host), os;
    for (char &c : low_host) c = char(std::tolower(static_cast<unsigned char>(c)));
    std::ifstream release("/etc/os-release");
    for (std::string line; std::getline(release, line);)
      if (line.starts_with("ID=") || line.starts_with("NAME=") || line.starts_with("PRETTY_NAME=")) os += line + " ";
    for (char &c : os) c = char(std::tolower(static_cast<unsigned char>(c)));
    distribution_name = os.find(low_host) != std::string::npos;
  }
#endif
  if (host && std::strlen(host) >= 3 && !distribution_name) names.push_back({host, "<computer>", true});
  for (const auto &p : private_text)
    if (p.size() >= 3) names.push_back({p, "<account>", true});
  std::ranges::sort(names, std::greater{}, [](const auto &n) { return n.text.size(); });
  // Paths and names compare without case, as Windows does.
  auto lower = [](std::string s) {
    for (char &c : s) c = char(std::tolower(static_cast<unsigned char>(c)));
    return s;
  };
  auto alnum = [](char c) { return std::isalnum(static_cast<unsigned char>(c)) != 0; };
  for (const auto &name : names) {
    std::string low = lower(text), key = lower(name.text);
    std::string out;
    size_t at = 0;
    for (size_t hit = 0; (hit = low.find(key, hit)) != std::string::npos;) {
      size_t end = hit + key.size();
      if (name.word && ((hit > 0 && alnum(low[hit - 1])) || (end < low.size() && alnum(low[end])))) {
        hit = end;
        continue;
      }
      out.append(text, at, hit - at).append(name.replacement);
      at = hit = end;
    }
    if (at == 0) continue;
    out.append(text, at);
    text = std::move(out);
  }
  // Anything shaped like a credential, line by line so ordinary lines skip
  // the expressions.
  static const std::regex token(R"(\b(OC|FRL)[A-Za-z0-9]{40,}|eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]*)");
  static const std::regex keyword(
      R"(((bearer|access_token|refresh_token|password|nonce)(["']?\s*[:=]\s*["']?|\s+))[^\s"',&]{8,})",
      std::regex::icase);
  std::string out;
  out.reserve(text.size());
  for (size_t at = 0; at < text.size();) {
    size_t end = text.find('\n', at);
    end = end == std::string::npos ? text.size() : end + 1;
    std::string_view line(text.data() + at, end - at);
    std::string low = lower(std::string(line));
    if (line.find("OC") != line.npos || line.find("FRL") != line.npos || line.find("eyJ") != line.npos ||
        low.find("bearer") != std::string::npos || low.find("token") != std::string::npos ||
        low.find("password") != std::string::npos || low.find("nonce") != std::string::npos)
      out += std::regex_replace(std::regex_replace(std::string(line), token, "<removed>"), keyword, "$1<removed>");
    else
      out += line;
    at = end;
  }
  return out;
}

fs::path write_debug_report(const DebugReport &request) {
  const fs::path logs = request.data / "logs";
  std::error_code ec;
  // The game: the one asked for, else the most recently written runtime log.
  std::string package = request.package;
  if (package.empty()) {
    // libstdc++'s file clock epoch is in 2174, so present times are negative.
    auto newest = fs::file_time_type::min();
    for (const auto &e : fs::directory_iterator(logs, ec)) {
      std::string name = paths::utf8(e.path().filename());
      if (!name.starts_with("runtime-") || !name.ends_with(".log") || name.ends_with(".prev.log") ||
          name.ends_with(".console.log"))
        continue;
      if (auto t = e.last_write_time(ec); !ec && t > newest) {
        newest = t;
        package = name.substr(8, name.size() - 12);
      }
    }
  }
  const std::string base = "runtime-" + package;
  const fs::path runtime_log = logs / (base + ".log"), console_log = logs / (base + ".console.log");

  auto now = Clock::now();
  std::string out = "RiftLift debug report\n";
  out += std::format("Created: {}\n", local_time(now, "%Y-%m-%d %H:%M:%S %z"));
  out += std::format("RiftLift: {}\n", request.version);
  if (package.empty()) out += "Game: none launched yet\n";
  else if (request.game.empty()) out += std::format("Game: {}\n", package);
  else out += std::format("Game: {} ({})\n", request.game, package);
  out += std::format("Debug logging: {}\n", request.debug_logging ? "on" : "off");

  // What happened, then why: the summary a reader needs first.
  std::optional<Run> run;
  std::optional<Window> window;
  if (!package.empty() && fs::exists(runtime_log, ec)) {
    run = read_run(runtime_log, console_log);
    const auto end = run->ended.value_or(now);
    auto start = run->started;
    // Logs from before this RiftLift recorded the start: the previous run ended before this one began.
    if (!start) start = modified(logs / (base + ".prev.log"));
    if (!start || *start > end || end - *start > std::chrono::hours(6)) start = end - std::chrono::hours(1);
    window = Window{*start - std::chrono::seconds(5), end + std::chrono::seconds(5)};
    out += std::format("Last run: {} to {} ({}{}){}\n", local_time(*start), local_time(end),
                       run->started ? "" : "start estimated, ", duration_text(end - *start),
                       run->version.empty() || run->version == request.version ? ""
                                                                               : ", by RiftLift runtime " + run->version);
    out += std::format("Result: {}\n", run->result);
  }

  std::string checks;
  if (!request.runtime.empty() && fs::exists(request.runtime, ec))
    checks = capture(request.runtime, {"--check"}, std::chrono::seconds(30));

  // Gathered before the summary is written, which they inform.
  std::string evidence_text, evidence;
  if (window) evidence = evidence_section(evidence_text, request.data, *window, run->failed);
  std::string summary_input = tail_log(runtime_log, 512 << 10) + "\n" + tail_log(console_log, 512 << 10) + "\n" + evidence;

  std::vector<std::string> steps;
  out += "\n=== Likely cause ===\n";
  if (!run) {
    out += package.empty() ? "No game has been started with this RiftLift yet.\n"
                           : std::format("{} has no run to report yet.\n", request.game.empty() ? package : request.game);
    steps.push_back("Start the game that has the problem, then send a new report.");
  } else if (auto d = diagnose(summary_input)) {
    out += d->cause + "\n";
    steps.push_back(d->advice);
  } else if (run->failed) {
    out += "No known failure signature: the errors below are the closest leads.\n";
  } else {
    out += "The last run did not fail. If the problem happened in an earlier run, start the game again, "
           "reproduce it, then send a new report.\n";
  }
  for (const auto &line : lines_of(checks)) {
    if (!line.starts_with("FAIL")) continue;
    if (line.find(" Steam:") != std::string::npos) steps.push_back("Install Steam and start it once.");
    else if (line.find(" Active OpenXR runtime:") != std::string::npos)
      steps.push_back("Set up a VR runtime (WiVRn, Monado or SteamVR) and make it the active OpenXR runtime.");
    else if (line.find(" Backend") == std::string::npos)
      steps.push_back("Start a game again: RiftLift repairs its compatibility components when a game starts.");
  }
  if (run && run->failed && !request.debug_logging)
    steps.push_back("Turn on Debug logging (Settings > Troubleshooting), start the game again, then send a new "
                    "report: it adds Proton, graphics and crash logs.");
  if (run && !run->version.empty() && run->version != request.version)
    steps.push_back(std::format("Start the game again with RiftLift {}: the last run used {}.", request.version,
                                run->version));
  std::ranges::sort(steps);
  steps.erase(std::ranges::unique(steps).begin(), steps.end());
  if (!steps.empty()) {
    out += "\n=== Next steps ===\n";
    for (const auto &s : steps) out += "- " + s + "\n";
  }

  out += "\n=== Checks ===\n";
  out += checks.empty() ? "(riftlift-runtime --check gave no answer)\n" : checks;
  if (!checks.empty() && checks.back() != '\n') out += '\n';
  out += "\n=== System ===\n";
  system_section(out);
  out += "\n=== VR runtime ===\n";
  xr_section(out);
  processes_section(out);
  out += "\n=== Environment ===\n";
  environment_section(out);
  library_section(out, request.data, package);
  {
    std::string text = read_file(request.data / "settings.json");
    if (!text.empty()) {
      out += "\n=== settings.json ===\n" + text;
      if (text.back() != '\n') out += '\n';
    }
  }
  if (!request.plan.empty()) {
    std::string text = read_file(request.plan);
    if (!text.empty()) out += std::format("\n=== Game record ===\n{}\n", text);
  }
  if (!package.empty()) {
    // Crash dumps hold memory from the game and stay on this PC; listing them
    // tells a developer to ask for one.
    std::string dumps;
    for (const auto &e : fs::directory_iterator(logs, ec)) {
      std::string name = paths::utf8(e.path().filename());
      if (name.starts_with(base + "-") && name.ends_with(".dmp"))
        dumps += std::format("{} ({} KiB)\n", name, (e.file_size(ec) + 1023) / 1024);
    }
    if (!dumps.empty()) out += "\n=== Crash dumps on this PC (not included) ===\n" + dumps;
  }
  if (window) {
    out += std::format("\n=== Errors logged during the last run ({} to {}) ===\n", local_time(window->from),
                       local_time(window->to));
    out += evidence_text.empty() ? "No other log written during the run reports an error.\n" : evidence_text;
  }
  if (!package.empty()) {
    log_section(out, runtime_log, kRuntimeLog);
    log_section(out, console_log, kSideLog);
    log_section(out, logs / (base + ".prev.log"), kSideLog);
    log_section(out, logs / (base + ".console.prev.log"), kSideLog);
  }
  log_section(out, logs / "launcher.log", kLauncherLog);
  // A game started from Steam or a shortcut logs its launcher separately.
  fs::path direct;
  auto direct_time = fs::file_time_type::min();
  for (const auto &e : fs::directory_iterator(logs, ec))
    if (paths::utf8(e.path().filename()).starts_with("launcher-direct-"))
      if (auto t = e.last_write_time(ec); !ec && t > direct_time) {
        direct = e.path();
        direct_time = t;
      }
  if (!direct.empty()) log_section(out, direct, kSideLog);

  const fs::path dir = request.data / "reports";
  fs::create_directories(dir);
  fs::path file = dir / std::format("riftlift-report-{}{}.txt", package.empty() ? "" : package + "-",
                                    local_time(now, "%Y%m%d-%H%M%S"));
  std::ofstream(file, std::ios::binary) << redact(std::move(out), request.private_text);
  prune(dir);
  return file;
}

std::string upload_debug_report(const fs::path &file, std::stop_token stop) {
  std::string text = read_file(file, kPasteLimit + 1);
  if (text.empty()) throw std::runtime_error("The report is empty.");
  if (text.size() > kPasteLimit) throw std::runtime_error("The report is too large to upload.");
  auto r = http::post("https://dpaste.com/api/v2/",
                      {{"content", text}, {"title", paths::utf8(file.filename())}, {"expiry_days", kPasteDays}},
                      stop);
  std::string url = r.body.substr(0, r.body.find_first_of("\r\n"));
  if (r.status != 201 || !url.starts_with("https://dpaste.com/"))
    throw std::runtime_error(std::format("The upload service answered {}.", r.status));
  return url;
}
} // namespace riftlift::launcher
