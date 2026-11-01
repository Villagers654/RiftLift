#include "runtime/xr.h"
#include "runtime/files.h"
#include "runtime/rules.h"
#include <random>
#include <stdexcept>
#ifdef _WIN32
#include <windows.h>
#else
#include <cerrno>
#include <fcntl.h>
#include <unistd.h>
#endif

namespace riftlift::runtime {
fs::path active_runtime_json(const Env &env) {
  if (auto explicit_value = strip(get(env, "XR_RUNTIME_JSON")); !explicit_value.empty()) {
    auto target = paths::from_utf8(explicit_value);
    std::error_code ec;
    if (!target.is_absolute() || !fs::is_regular_file(target, ec))
      throw std::runtime_error("XR_RUNTIME_JSON does not name an existing absolute file: " +
                               explicit_value);
    return fs::canonical(target);
  }
  auto candidate = xdg_config_home() / "openxr/1/active_runtime.json";
  std::error_code ec;
  if (fs::is_regular_file(candidate, ec))
    return fs::canonical(candidate);
  throw std::runtime_error("no active OpenXR runtime is configured at " + paths::utf8(candidate) +
                           "; select one with your runtime manager or set XR_RUNTIME_JSON to its "
                           "absolute manifest path");
}
std::optional<fs::path> steamvr_runtime_for_openxr(const fs::path &manifest) {
  try {
    auto payload = parse_json(read_file(manifest));
    const auto &description = payload.at("runtime");
    if (!description.is_object())
      return std::nullopt;
    const bool steamvr = field(description, "VALVE_runtime_is_steamvr") == true ||
                         lower(str(description, "name")) == "steamvr";
    if (!steamvr)
      return std::nullopt;
    auto root = fs::canonical(manifest).parent_path();
    std::error_code ec;
    if (!fs::is_regular_file(root / "bin/linux64/vrclient.so", ec))
      return std::nullopt;
    return root;
  } catch (...) {
    return std::nullopt;
  }
}
namespace {
// Creates `file` with `text` only if it does not exist yet; false if it did.
bool create_new(const fs::path &file, const std::string &text) {
#ifdef _WIN32
  HANDLE h = CreateFileW(file.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                         FILE_ATTRIBUTE_NORMAL, nullptr);
  if (h == INVALID_HANDLE_VALUE) {
    if (GetLastError() == ERROR_FILE_EXISTS)
      return false;
    throw std::runtime_error("Could not create " + paths::utf8(file) + ".");
  }
  DWORD written = 0;
  const bool ok = WriteFile(h, text.data(), DWORD(text.size()), &written, nullptr);
  CloseHandle(h);
#else
  int fd = ::open(file.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
  if (fd < 0) {
    if (errno == EEXIST)
      return false;
    throw std::runtime_error("Could not create " + paths::utf8(file) + ".");
  }
  const bool ok = ::write(fd, text.data(), text.size()) == ssize_t(text.size());
  ::close(fd);
#endif
  if (!ok)
    throw std::runtime_error("Could not write " + paths::utf8(file) + ".");
  return true;
}
} // namespace
std::string platform_user_id(const Layout &layout, const Env &env) {
  if (auto value = strip(get(env, "RIFTLIFT_USER_ID")); !value.empty())
    return validate_user_id(value, "RIFTLIFT_USER_ID");
  const auto file = layout.config / "platform-user-id";
  std::error_code ec;
  if (fs::is_regular_file(file, ec))
    return validate_user_id(read_file(file), paths::utf8(file));
  fs::create_directories(layout.config);
  std::random_device device;
  std::uint64_t bits = (std::uint64_t(device()) << 32) | device();
  // 62 random bits with bit 61 set: always large enough for engines that
  // reject small Oculus IDs.
  auto generated = std::to_string((bits & ((std::uint64_t(1) << 62) - 1)) | (std::uint64_t(1) << 61));
  if (!create_new(file, generated + "\n"))
    return validate_user_id(read_file(file), paths::utf8(file));
  return generated;
}
} // namespace riftlift::runtime
