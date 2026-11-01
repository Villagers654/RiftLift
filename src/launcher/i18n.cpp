#include "launcher/i18n.h"
#include <algorithm>
#include <cstdlib>
#include <span>
#include <unordered_map>
#ifdef _WIN32
#include <windows.h>
#endif
namespace riftlift::launcher::i18n {
// i18n_fr.cpp: {English, French} pairs.
std::span<const std::pair<std::string_view, std::string_view>> french();
namespace {
std::string g_language = "en";
const std::unordered_map<std::string_view, std::string_view> *g_table = nullptr;
std::string system_language() {
#ifdef _WIN32
  // The display language, not the regional format.
  wchar_t name[LOCALE_NAME_MAX_LENGTH] = {};
  if (LCIDToLocaleName(MAKELCID(GetUserDefaultUILanguage(), SORT_DEFAULT), name, LOCALE_NAME_MAX_LENGTH, 0) > 1)
    return {char(name[0]), char(name[1])};
#else
  // gettext's precedence: LANGUAGE (a list; its first entry), unless the
  // locale is C; then LC_ALL, LC_MESSAGES, LANG.
  std::string locale;
  for (const char *variable : {"LC_ALL", "LC_MESSAGES", "LANG"})
    if (const char *value = std::getenv(variable); value && *value) {
      locale = value;
      break;
    }
  if (const char *list = std::getenv("LANGUAGE"); list && *list && locale != "C" && locale != "POSIX")
    return std::string(list).substr(0, 2);
  if (!locale.empty())
    return locale.substr(0, 2);
#endif
  return "en";
}
const std::unordered_map<std::string_view, std::string_view> &french_table() {
  static const auto table = [] {
    std::unordered_map<std::string_view, std::string_view> out;
    for (const auto &[english, translated] : french())
      out.emplace(english, translated);
    return out;
  }();
  return table;
}
} // namespace
void set_language(std::string_view setting) {
  std::string code = setting == "auto" || setting.empty() ? system_language() : std::string(setting);
  std::ranges::transform(code, code.begin(), [](unsigned char c) { return char(std::tolower(c)); });
  if (code == "fr") {
    g_language = "fr";
    g_table = &french_table();
  } else {
    g_language = "en";
    g_table = nullptr;
  }
}
std::string_view language() { return g_language; }
std::string_view tr(std::string_view english) {
  if (!g_table || english.empty())
    return english;
  auto found = g_table->find(english);
  return found == g_table->end() ? english : found->second;
}
} // namespace riftlift::launcher::i18n
