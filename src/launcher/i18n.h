#pragma once
// Interface translations. Text is written in English throughout the UI; the
// drawing helpers (ui/style.cpp) pass every string they draw or measure
// through tr(), so a string with an entry in the active language's table is
// shown translated and anything else (game names, paths) is shown as is.
#include <format>
#include <string>
#include <string_view>
namespace riftlift::launcher::i18n {
// "auto" follows the system language; otherwise a code from languages().
void set_language(std::string_view setting);
// The active language's code ("en" when nothing else applies).
std::string_view language();
// The translation of `english` in the active language, else `english` itself.
std::string_view tr(std::string_view english);
// tr() for std::format patterns: the translated pattern keeps the arguments'
// order and count.
template <class... Args> std::string trf(std::format_string<Args...> pattern, Args &&...args) {
  return std::vformat(tr(pattern.get()), std::make_format_args(args...));
}
} // namespace riftlift::launcher::i18n
