#pragma once
// Child processes with an explicit environment, working directory and process
// group: what the game launch needs beyond common/process.h (run_tool always
// inherits this process's environment).
#include "common/paths.h"
#include <chrono>
#include <map>
#include <memory>
#include <optional>
#include <stop_token>
#include <string>
#include <vector>

namespace riftlift::runtime {

// Windows treats variable names case-insensitively; one entry per name there.
struct EnvLess {
  bool operator()(const std::string &a, const std::string &b) const;
};
using Env = std::map<std::string, std::string, EnvLess>;
// This process's environment, as Python's os.environ.copy() saw it.
Env current_environment();
// The value of `name`, or empty.
std::string get(const Env &env, const std::string &name);

// An executable named on PATH (or a path containing a separator), like
// shutil.which; empty when there is none.
fs::path find_program(std::string_view name, const Env &env);

struct SpawnOptions {
  fs::path cwd;                  // empty: this process's
  const Env *env = nullptr;      // null: this process's
  bool own_group = false;        // a new session/process group (job on Windows)
  bool quiet = false;            // stdout/stderr to the null device
  fs::path output;               // or: stdout/stderr to this file (truncated)
};

class Child {
public:
  Child();
  ~Child();
  Child(Child &&) noexcept;
  Child &operator=(Child &&) noexcept;
  // argv[0] is passed to the program as is; `executable` is what runs.
  void spawn(const fs::path &executable, const std::vector<std::string> &argv,
             const SpawnOptions &options = {});
  bool running() const;
  std::optional<int> poll();
  // Waits until the child exits, `stop` is requested or `timeout` passes;
  // nothing means it is still running.
  std::optional<int> wait(std::stop_token stop = {},
                          std::optional<std::chrono::milliseconds> timeout = {});
  // SIGTERM to its group (POSIX); on Windows the job is terminated outright.
  void terminate();
  void kill();
  // terminate(), then kill() if it is still running after `grace`; reaps it.
  int stop(std::chrono::milliseconds grace = std::chrono::seconds(5));
  long pid() const;
  // Lets it run on beyond this object (still reaped on POSIX).
  void detach();

private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

// Runs a program to completion. A stop request or the timeout stops it and
// throws; otherwise returns its exit code.
int run(const fs::path &executable, const std::vector<std::string> &argv,
        const SpawnOptions &options = {}, std::stop_token stop = {},
        std::optional<std::chrono::milliseconds> timeout = {});

// Sleeps for `duration` unless a stop is requested first; false if stopped.
bool sleep_for(std::chrono::milliseconds duration, std::stop_token stop);

} // namespace riftlift::runtime
