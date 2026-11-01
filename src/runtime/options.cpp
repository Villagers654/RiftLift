#include "runtime/options.h"
#include <charconv>
#include <stdexcept>

namespace riftlift::runtime {
namespace {
std::uint32_t number(const std::string &option, const std::string &value) {
  std::uint32_t n = 0;
  auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), n);
  if (value.empty() || error != std::errc{} || end != value.data() + value.size())
    throw std::invalid_argument(option + " needs a number, not '" + value + "'.");
  return n;
}
std::uintptr_t handle(const std::string &option, const std::string &value) {
  std::uintptr_t n = 0;
  auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), n);
  if (value.empty() || error != std::errc{} || end != value.data() + value.size() || !n)
    throw std::invalid_argument(option + " needs a handle, not '" + value + "'.");
  return n;
}
} // namespace
Options parse_options(const std::vector<std::string> &args) {
  Options o;
  for (size_t i = 0; i < args.size(); ++i) {
    const auto &option = args[i];
    auto value = [&]() -> std::string {
      if (i + 1 >= args.size())
        throw std::invalid_argument(option + " needs a value.");
      return args[++i];
    };
    if (option == "--game")
      o.game = value();
    else if (option == "--log")
      o.log = value();
    else if (option == "--account")
      o.account = value();
    else if (option == "--parent-pid")
      o.parent_pid = number(option, value());
    else if (option == "--console")
      o.console = true;
    else if (option == "--run-seconds")
      o.run_seconds = number(option, value());
    else if (option == "--check")
      o.check = true;
    else if (option == "--probe-openvr")
      o.probe_openvr = value();
    else if (option == "--credential-read")
      o.credential_read = handle(option, value());
    else if (option == "--credential-write")
      o.credential_write = handle(option, value());
    // Accepted for compatibility: the package is the game.
    else if (option == "--package")
      o.ignored.push_back(option + " " + value());
    else
      o.ignored.push_back(option);
  }
  if (!o.credential_read != !o.credential_write)
    throw std::invalid_argument("Both credential channel handles are required.");
  if (o.game.empty() && o.probe_openvr.empty() && !o.check)
    throw std::invalid_argument("--game is required.");
  return o;
}
} // namespace riftlift::runtime
