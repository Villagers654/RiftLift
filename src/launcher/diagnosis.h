#pragma once
// Known causes of a failed game launch, recognised in its logs.
#include <optional>
#include <string>
#include <string_view>

namespace riftlift::launcher {
struct Diagnosis {
  std::string cause;   // what went wrong, in English (tr() at display)
  std::string advice;  // what to try next
};
// The first known failure signature in `evidence` (a run's runtime, console,
// Proton and game logs and the kernel journal), most specific first; nothing
// when no rule matches.
std::optional<Diagnosis> diagnose(std::string_view evidence);
// Whether a log line reports a failure (error, crash, timeout...).
bool error_line(std::string_view line);
// Lines that look like errors but are expected noise.
bool noisy_line(std::string_view line);
} // namespace riftlift::launcher
