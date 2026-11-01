#include "launcher/diagnosis.h"
#include <algorithm>
#include <array>
#include <cctype>
#include <regex>

namespace riftlift::launcher {
namespace {
std::string lower(std::string_view text) {
  std::string out(text);
  for (char &c : out) c = char(std::tolower(static_cast<unsigned char>(c)));
  return out;
}
bool word_char(char c) { return std::isalnum(static_cast<unsigned char>(c)) || c == '_'; }

struct Rule {
  std::array<std::string_view, 4> signatures;  // any one matches; lower case
  std::string_view cause, advice;
};
// Most specific first: the first rule that matches names the cause.
constexpr Rule kRules[] = {
    {{"riftlift: patched 0 executable runtime imports"},
     "RiftLift could not hook the game's Oculus runtime calls.",
     "Send a debug report: this game needs a compatibility fix."},
    {{"non-oculus openxr runtime is not supported"},
     "The game's Unity Oculus plugin only accepts Meta's own OpenXR runtime.",
     "Send a debug report: this game needs a compatibility fix."},
    {{"xr_error_api_version_unsupported"},
     "Your OpenXR runtime does not support the OpenXR version the game asked for.",
     "Update your VR runtime (WiVRn, Monado or SteamVR), then start the game again."},
    {{"xr_error_runtime_unavailable", "xrcreateinstance failed: -51", "openxr result -51"},
     "Your VR runtime was not running when the game started VR.",
     "Start your VR runtime and connect the headset, then start the game again."},
    {{"failed to inject", "failed to create process"},
     "RiftLift's launcher could not start the game or load its compatibility layer.",
     "Send a debug report."},
    {{"gpu reset", "ring timeout", "vm fault", "illegal opcode in command stream"},
     "The graphics driver reset the GPU during the game.",
     "Update the graphics driver and turn off overlays (MangoHud, vkBasalt, OBS capture), then try again."},
    {{"vk_error_device_lost", "device lost"},
     "The game lost the graphics device.",
     "Update the graphics driver and turn off overlays (MangoHud, vkBasalt, OBS capture), then try again."},
    {{"xr_error_form_factor_unavailable"},
     "Your VR runtime had no headset connected.",
     "Connect the headset and make sure it is tracking, then start the game again."},
    {{"xr_error_graphics_device_invalid"},
     "The game and your VR runtime are using different GPUs.",
     "Run both on the same GPU (check settings such as DRI_PRIME), then try again."},
    {{"out_of_device_memory", "out of device memory"},
     "The GPU ran out of memory.",
     "Close other programs that use the GPU or lower the headset resolution, then try again."},
};
} // namespace

bool error_line(std::string_view line) {
  static constexpr std::string_view words[] = {
      "error",     "errors",      "fail",          "failed",    "failure",  "fatal",     "panic",
      "crash",     "crashed",     "exception",     "timed out", "time out", "timeout",   "unsupported",
      "not supported", "not found", "device lost", "segfault",  "denied",   "gpu reset", "vm fault",
      "page fault", "hung",       "oom",           "xid"};
  const std::string text = lower(line);
  for (auto word : words)
    for (size_t at = 0; (at = text.find(word, at)) != std::string::npos; at += word.size()) {
      const size_t end = at + word.size();
      if ((at == 0 || !word_char(text[at - 1])) && (end == text.size() || !word_char(text[end])))
        return true;
    }
  return false;
}

bool noisy_line(std::string_view line) {
  for (auto noise : {"Failed to parse bindings for ViveController",
                     "Failed to parse bindings for Unknown(\"holographic_controller\")",
                     "Unsupported application type: Utility", "riftlift-validate-", "riftlift-probe-",
                     "Listening on systemd-oomd", "fixme:"})
    if (line.find(noise) != std::string_view::npos) return true;
  return line.find("Registered") != std::string_view::npos && line.find("drm panic") != std::string_view::npos;
}

std::optional<Diagnosis> diagnose(std::string_view evidence) {
  const std::string text = lower(evidence);
  for (const auto &rule : kRules)
    for (auto signature : rule.signatures)
      if (!signature.empty() && text.find(signature) != std::string::npos)
        return Diagnosis{std::string(rule.cause), std::string(rule.advice)};
  // The game's own report that VR did not start; a crash after it is a
  // consequence, not the cause.
  static const std::regex vr_failed(
      R"(failed to initiali[sz]e.{0,100}(oculus|ovr|openxr|vr (api|library|runtime|session))|failed to initiali[sz]e (oculus|ovr|openxr|vr))");
  for (size_t at = 0; at < text.size();) {
    size_t end = text.find('\n', at);
    if (end == std::string::npos) end = text.size();
    std::string_view line(text.data() + at, end - at);
    if (line.find("failed to initiali") != std::string_view::npos &&
        std::regex_search(line.begin(), line.end(), vr_failed))
      return Diagnosis{"The game reported that it could not start VR.",
                       "Start your VR runtime and connect the headset, then start the game again."};
    at = end + 1;
  }
  return std::nullopt;
}
} // namespace riftlift::launcher
