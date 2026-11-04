#include "launcher/sign_in_link.h"
#include "common/environment.h"
#include "common/process.h"
#include <algorithm>
#include <cctype>
#include <fstream>
#include <sstream>
#ifdef _WIN32
#include <windows.h>
#else
#include <ranges>
#include <unistd.h>
#endif

namespace riftlift::launcher::sign_in_link {
namespace {
// The address a browser handed over, and the handler to put back.
fs::path inbox(const fs::path &data) { return data / "sign-in-link"; }
fs::path previous_file(const fs::path &data) { return data / "sign-in-link.previous"; }

std::string read_all(const fs::path &file) {
  std::ifstream in(file, std::ios::binary);
  std::ostringstream out;
  out << in.rdbuf();
  return out.str();
}

#ifdef _WIN32
constexpr wchar_t kKey[] = L"Software\\Classes\\oculus";
constexpr wchar_t kCommandKey[] = L"Software\\Classes\\oculus\\shell\\open\\command";

std::wstring command_value() {
  DWORD size = 0;
  if (RegGetValueW(HKEY_CURRENT_USER, kCommandKey, nullptr, RRF_RT_REG_SZ, nullptr, nullptr, &size) != ERROR_SUCCESS)
    return {};
  std::wstring value(size / sizeof(wchar_t), L'\0');
  if (RegGetValueW(HKEY_CURRENT_USER, kCommandKey, nullptr, RRF_RT_REG_SZ, nullptr, value.data(), &size) != ERROR_SUCCESS)
    return {};
  value.resize(wcsnlen(value.c_str(), value.size()));
  return value;
}
bool set(const wchar_t *key, const wchar_t *name, const std::wstring &value) {
  return RegSetKeyValueW(HKEY_CURRENT_USER, key, name, REG_SZ, value.c_str(),
                         DWORD((value.size() + 1) * sizeof(wchar_t))) == ERROR_SUCCESS;
}
#else
// The handler's desktop entry, beside the user's other applications.
constexpr char kEntry[] = "io.github.villagers654.RiftLift.SignIn.desktop";
constexpr char kScheme[] = "x-scheme-handler/oculus";

fs::path user_data() {
  if (auto p = environment::getenv("XDG_DATA_HOME"); p && fs::path(p).is_absolute()) return p;
  auto home = environment::getenv("HOME");
  return home ? fs::path(home) / ".local/share" : fs::path();
}
fs::path user_config() {
  if (auto p = environment::getenv("XDG_CONFIG_HOME"); p && fs::path(p).is_absolute()) return p;
  auto home = environment::getenv("HOME");
  return home ? fs::path(home) / ".config" : fs::path();
}
fs::path which(std::string_view name) {
  const char *path = environment::getenv("PATH");
  for (auto dir : std::views::split(std::string_view(path ? path : "/usr/bin:/bin"), ':')) {
    fs::path candidate = fs::path(std::string(dir.begin(), dir.end())) / name;
    if (access(candidate.c_str(), X_OK) == 0) return candidate;
  }
  return {};
}
// xdg-mime's output; false when it fails.
bool xdg_mime(const std::vector<std::string> &args, std::string *out = nullptr) {
  auto tool = which("xdg-mime");
  if (tool.empty()) return false;
  std::string text;
  try {
    int code = run_tool(tool, args, [&](std::string_view chunk) { text += chunk; });
    while (!text.empty() && std::isspace(static_cast<unsigned char>(text.back()))) text.pop_back();
    if (out) *out = text;
    return code == 0;
  } catch (...) {
    return false;
  }
}
// One Exec= argument, quoted as the desktop entry spec asks.
std::string exec_arg(std::string_view arg) {
  std::string out = "\"";
  for (char c : arg) {
    if (c == '"' || c == '`' || c == '$' || c == '\\') out += "\\\\";  // escaped twice: quoting, then the string
    if (c == '%') out += '%';
    out += c;
  }
  return out + '"';
}
// What restore() leaves when there was no handler before: no default at all.
void forget_default() {
  auto list = user_config() / "mimeapps.list";
  std::error_code ec;
  if (user_config().empty() || !fs::exists(list, ec)) return;
  std::istringstream in(read_all(list));
  std::string out, line;
  const std::string ours = std::string(kScheme) + "=" + kEntry;
  bool changed = false;
  while (std::getline(in, line)) {
    if (line == ours || line == ours + ";") {
      changed = true;
      continue;
    }
    out += line + '\n';
  }
  if (changed) paths::atomic_write(list, out);
}
#endif
} // namespace

bool take_over(const fs::path &data) {
  (void)receive(data);  // an address from an earlier sign-in
#ifdef _WIN32
  // Per user, so it needs no administrator, and it shadows the Meta app's own.
  // One left by a sign-in RiftLift never finished keeps the handler from before it.
  std::error_code ec;
  auto before = command_value();
  const bool ours = before.find(L"--sign-in-callback") != std::wstring::npos;
  if (!ours || !fs::exists(previous_file(data), ec))
    paths::atomic_write(previous_file(data), ours ? std::string() : paths::utf8(fs::path(before)));
  const std::wstring command = L"\"" + paths::executable().wstring() + L"\" --sign-in-callback \"" +
                               data.wstring() + L"\" \"%1\"";
  return set(kKey, nullptr, L"URL:oculus") && set(kKey, L"URL Protocol", L"") && set(kCommandKey, nullptr, command);
#else
  // The sandbox has no say over the host's handlers.
  if (environment::getenv("FLATPAK_ID") || user_data().empty()) return false;
  if (which("xdg-mime").empty()) return false;
  // One left by a sign-in RiftLift never finished keeps the handler from before it.
  std::error_code ec;
  std::string before;
  xdg_mime({"query", "default", kScheme}, &before);
  const bool ours = before == kEntry;
  if (!ours || !fs::exists(previous_file(data), ec))
    paths::atomic_write(previous_file(data), ours ? std::string() : before);
  const char *image = environment::getenv("APPIMAGE");
  const fs::path program = image && *image ? paths::from_utf8(image) : paths::executable();
  paths::atomic_write(user_data() / "applications" / kEntry,
                      "[Desktop Entry]\nType=Application\nName=RiftLift sign-in\nNoDisplay=true\n"
                      "Exec=" + exec_arg(paths::utf8(program)) + " --sign-in-callback " +
                          exec_arg(paths::utf8(data)) + " %u\n"
                      "MimeType=" + kScheme + ";\n");
  return xdg_mime({"default", kEntry, kScheme});
#endif
}

void restore(const fs::path &data) {
  std::error_code ec;
#ifndef _WIN32
  fs::remove(user_data() / "applications" / kEntry, ec);
#endif
  if (!fs::exists(previous_file(data), ec)) return;
  const auto before = read_all(previous_file(data));
  fs::remove(previous_file(data), ec);
#ifdef _WIN32
  if (before.empty())
    RegDeleteTreeW(HKEY_CURRENT_USER, kKey);
  else
    set(kCommandKey, nullptr, paths::from_utf8(before).wstring());
#else
  if (before.empty())
    forget_default();
  else
    xdg_mime({"default", before, kScheme});
#endif
}

int deliver(const fs::path &data, std::string_view address) {
  constexpr std::string_view scheme = "oculus://";
  auto lower = [](char c) { return char(std::tolower(static_cast<unsigned char>(c))); };
  if (!data.is_absolute() || address.size() > 16 * 1024 || address.size() < scheme.size() ||
      !std::ranges::equal(address.substr(0, scheme.size()), scheme, {}, lower))
    return 2;
  try {
    paths::atomic_write(inbox(data), address);
    return 0;
  } catch (...) {
    return 1;
  }
}

std::string receive(const fs::path &data) {
  std::error_code ec;
  if (!fs::exists(inbox(data), ec)) return {};
  auto address = read_all(inbox(data));
  fs::remove(inbox(data), ec);
  return address;
}
} // namespace riftlift::launcher::sign_in_link
