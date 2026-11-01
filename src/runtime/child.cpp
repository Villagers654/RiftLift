#include "runtime/child.h"
#include "common/process.h"
#include <algorithm>
#include <stdexcept>
#include <thread>
#ifdef _WIN32
#include <windows.h>
#else
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
#endif

namespace riftlift::runtime {
using namespace std::chrono_literals;

bool EnvLess::operator()(const std::string &a, const std::string &b) const {
#ifdef _WIN32
  return std::lexicographical_compare(a.begin(), a.end(), b.begin(), b.end(), [](char x, char y) {
    return std::toupper(static_cast<unsigned char>(x)) < std::toupper(static_cast<unsigned char>(y));
  });
#else
  return a < b;
#endif
}
std::string get(const Env &env, const std::string &name) {
  auto it = env.find(name);
  return it == env.end() ? std::string{} : it->second;
}
Env current_environment() {
  Env env;
#ifdef _WIN32
  auto *block = GetEnvironmentStringsW();
  for (auto *p = block; p && *p; p += wcslen(p) + 1) {
    std::wstring_view entry(p);
    auto equals = entry.find(L'=', 1);  // "=C:=C:\" style entries start with '='
    if (entry.starts_with(L'=') || equals == entry.npos)
      continue;
    env.emplace(paths::utf8(fs::path(entry.substr(0, equals))),
                paths::utf8(fs::path(entry.substr(equals + 1))));
  }
  if (block)
    FreeEnvironmentStringsW(block);
#else
  for (char **e = environ; e && *e; ++e) {
    std::string_view entry(*e);
    auto equals = entry.find('=');
    if (equals == entry.npos || equals == 0)
      continue;
    env.emplace(std::string(entry.substr(0, equals)), std::string(entry.substr(equals + 1)));
  }
#endif
  return env;
}
fs::path find_program(std::string_view name, const Env &env) {
  if (name.empty())
    return {};
  std::error_code ec;
  auto runnable = [&](const fs::path &candidate) {
#ifdef _WIN32
    return fs::is_regular_file(candidate, ec);
#else
    return fs::is_regular_file(candidate, ec) && access(candidate.c_str(), X_OK) == 0;
#endif
  };
#ifdef _WIN32
  constexpr char separator = ';';
  const bool has_directory = name.find_first_of("/\\") != name.npos;
  const std::vector<std::string> suffixes{"", ".exe", ".cmd", ".bat"};
#else
  constexpr char separator = ':';
  const bool has_directory = name.find('/') != name.npos;
  const std::vector<std::string> suffixes{""};
#endif
  if (has_directory) {
    for (auto &suffix : suffixes)
      if (auto candidate = paths::from_utf8(std::string(name) + suffix); runnable(candidate))
        return candidate;
    return {};
  }
  const auto storage = get(env, "PATH");
  std::string_view search = storage;
  while (!search.empty()) {
    auto end = search.find(separator);
    auto directory = search.substr(0, end);
    search = end == search.npos ? std::string_view{} : search.substr(end + 1);
    if (directory.empty())
      continue;
    for (auto &suffix : suffixes)
      if (auto candidate = paths::from_utf8(directory) / paths::from_utf8(std::string(name) + suffix);
          runnable(candidate))
        return candidate;
  }
  return {};
}
bool sleep_for(std::chrono::milliseconds duration, std::stop_token stop) {
  auto deadline = std::chrono::steady_clock::now() + duration;
  while (!stop.stop_requested()) {
    auto now = std::chrono::steady_clock::now();
    if (now >= deadline)
      return true;
    std::this_thread::sleep_for(std::min<std::chrono::steady_clock::duration>(deadline - now, 50ms));
  }
  return false;
}

#ifdef _WIN32
struct Child::Impl {
  HANDLE process = nullptr, job = nullptr;
  DWORD id = 0;
  std::optional<int> exit;
  ~Impl() {
    if (process) CloseHandle(process);
    if (job) CloseHandle(job);
  }
};
namespace {
HANDLE inheritable(HANDLE source) {
  HANDLE copy = nullptr;
  if (!source || source == INVALID_HANDLE_VALUE ||
      !DuplicateHandle(GetCurrentProcess(), source, GetCurrentProcess(), &copy, 0, TRUE,
                       DUPLICATE_SAME_ACCESS))
    return nullptr;
  return copy;
}
HANDLE open_output(const fs::path &file) {
  SECURITY_ATTRIBUTES sa{sizeof sa, nullptr, TRUE};
  return CreateFileW(file.empty() ? L"NUL" : file.c_str(), GENERIC_WRITE,
                     FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, &sa,
                     file.empty() ? OPEN_EXISTING : CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
}
} // namespace
void Child::spawn(const fs::path &executable, const std::vector<std::string> &argv,
                  const SpawnOptions &options) {
  if (running())
    throw std::logic_error("child already running");
  std::wstring command;
  for (auto &argument : argv) {
    if (!command.empty())
      command += L' ';
    command += quote_windows_arg(paths::from_utf8(argument).wstring());
  }
  std::wstring block;
  if (options.env) {
    for (auto &[key, value] : *options.env)
      block += paths::from_utf8(key + "=" + value).wstring() + L'\0';
    block += L'\0';
  }
  HANDLE output = nullptr;
  if (options.quiet || !options.output.empty()) {
    output = open_output(options.quiet ? fs::path{} : options.output);
    if (output == INVALID_HANDLE_VALUE)
      throw std::runtime_error("Could not open " + paths::utf8(options.output) + ".");
  }
  HANDLE in = inheritable(GetStdHandle(STD_INPUT_HANDLE));
  HANDLE out = output ? output : inheritable(GetStdHandle(STD_OUTPUT_HANDLE));
  HANDLE err = output ? inheritable(output) : inheritable(GetStdHandle(STD_ERROR_HANDLE));
  STARTUPINFOW si{};
  si.cb = sizeof si;
  si.dwFlags = STARTF_USESTDHANDLES;
  si.hStdInput = in;
  si.hStdOutput = out;
  si.hStdError = err;
  PROCESS_INFORMATION pi{};
  DWORD flags = CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW | CREATE_SUSPENDED;
  const auto cwd = options.cwd.empty() ? std::wstring{} : options.cwd.wstring();
  const BOOL created = CreateProcessW(executable.c_str(), command.data(), nullptr, nullptr, TRUE,
                                      flags, options.env ? block.data() : nullptr,
                                      cwd.empty() ? nullptr : cwd.c_str(), &si, &pi);
  const DWORD error = GetLastError();
  for (HANDLE h : {in, out, err})
    if (h) CloseHandle(h);
  if (!created)
    throw std::runtime_error("Could not start " + paths::utf8(executable.filename()) +
                             " (Windows error " + std::to_string(error) + ").");
  impl_ = std::make_unique<Impl>();
  impl_->process = pi.hProcess;
  impl_->id = pi.dwProcessId;
  if (options.own_group) {
    // The game's whole process tree goes with it, also if this process dies.
    impl_->job = CreateJobObjectW(nullptr, nullptr);
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (impl_->job)
      SetInformationJobObject(impl_->job, JobObjectExtendedLimitInformation, &limits, sizeof limits);
    if (impl_->job && !AssignProcessToJobObject(impl_->job, pi.hProcess)) {
      CloseHandle(impl_->job);
      impl_->job = nullptr;
    }
  }
  ResumeThread(pi.hThread);
  CloseHandle(pi.hThread);
}
std::optional<int> Child::poll() {
  if (running() && WaitForSingleObject(impl_->process, 0) == WAIT_OBJECT_0) {
    DWORD code = 0;
    GetExitCodeProcess(impl_->process, &code);
    impl_->exit = int(code);
  }
  return impl_->exit;
}
bool Child::running() const { return impl_->process && !impl_->exit; }
void Child::terminate() {
  if (!running())
    return;
  if (impl_->job)
    TerminateJobObject(impl_->job, 1);
  else
    TerminateProcess(impl_->process, 1);
}
void Child::kill() { terminate(); }
void Child::detach() { impl_ = std::make_unique<Impl>(); }
long Child::pid() const { return long(impl_->id); }
#else
struct Child::Impl {
  pid_t pid = -1;
  bool group = false;
  std::optional<int> exit;
};
void Child::spawn(const fs::path &executable, const std::vector<std::string> &argv,
                  const SpawnOptions &options) {
  if (running())
    throw std::logic_error("child already running");
  std::vector<std::string> storage(argv);
  std::vector<char *> args;
  for (auto &a : storage)
    args.push_back(a.data());
  args.push_back(nullptr);
  std::vector<std::string> variables;
  std::vector<char *> envp;
  if (options.env) {
    for (auto &[key, value] : *options.env)
      variables.push_back(key + "=" + value);
    for (auto &v : variables)
      envp.push_back(v.data());
    envp.push_back(nullptr);
  }
  posix_spawn_file_actions_t actions;
  posix_spawn_file_actions_init(&actions);
  if (options.quiet || !options.output.empty()) {
    posix_spawn_file_actions_addopen(&actions, 1, options.quiet ? "/dev/null" : options.output.c_str(),
                                     O_WRONLY | O_CREAT | O_TRUNC, 0600);
    posix_spawn_file_actions_adddup2(&actions, 1, 2);
  }
  if (!options.cwd.empty())
    posix_spawn_file_actions_addchdir_np(&actions, options.cwd.c_str());
  posix_spawnattr_t attributes;
  posix_spawnattr_init(&attributes);
  sigset_t defaults, empty;
  sigemptyset(&defaults);
  for (int signal : {SIGTERM, SIGINT, SIGHUP, SIGPIPE, SIGCHLD})
    sigaddset(&defaults, signal);
  sigemptyset(&empty);
  posix_spawnattr_setsigdefault(&attributes, &defaults);
  posix_spawnattr_setsigmask(&attributes, &empty);
  short flags = POSIX_SPAWN_SETSIGDEF | POSIX_SPAWN_SETSIGMASK;
  if (options.own_group)
    flags |= POSIX_SPAWN_SETSID;
  posix_spawnattr_setflags(&attributes, flags);
  pid_t pid = -1;
  const int error = posix_spawn(&pid, executable.c_str(), &actions, &attributes, args.data(),
                                options.env ? envp.data() : environ);
  posix_spawn_file_actions_destroy(&actions);
  posix_spawnattr_destroy(&attributes);
  if (error)
    throw std::runtime_error("Could not start " + executable.string() + ": " +
                             std::generic_category().message(error) + ".");
  impl_ = std::make_unique<Impl>();
  impl_->pid = pid;
  impl_->group = options.own_group;
}
std::optional<int> Child::poll() {
  int status = 0;
  if (running()) {
    pid_t result;
    while ((result = waitpid(impl_->pid, &status, WNOHANG)) < 0 && errno == EINTR) {
    }
    if (result == impl_->pid)
      impl_->exit = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    else if (result < 0)
      impl_->exit = 255;  // not our child any more; nothing left to wait for
  }
  return impl_->exit;
}
bool Child::running() const { return impl_->pid > 0 && !impl_->exit; }
void Child::terminate() {
  if (running())
    impl_->group ? ::killpg(impl_->pid, SIGTERM) : ::kill(impl_->pid, SIGTERM);
}
void Child::kill() {
  if (running())
    impl_->group ? ::killpg(impl_->pid, SIGKILL) : ::kill(impl_->pid, SIGKILL);
}
long Child::pid() const { return long(impl_->pid); }
void Child::detach() {
  if (running())
    std::thread([pid = impl_->pid] {
      while (waitpid(pid, nullptr, 0) < 0 && errno == EINTR) {
      }
    }).detach();
  impl_ = std::make_unique<Impl>();
}
#endif

Child::Child() : impl_(std::make_unique<Impl>()) {}
Child::~Child() {
  // Never leave a zombie or an orphaned tool behind an exception.
  if (impl_ && running())
    stop(2s);
}
Child::Child(Child &&) noexcept = default;
Child &Child::operator=(Child &&) noexcept = default;
std::optional<int> Child::wait(std::stop_token stop, std::optional<std::chrono::milliseconds> timeout) {
  const auto deadline = timeout ? std::chrono::steady_clock::now() + *timeout
                                : std::chrono::steady_clock::time_point::max();
  auto delay = 1ms;
  while (true) {
    if (auto code = poll())
      return code;
    if (stop.stop_requested() || std::chrono::steady_clock::now() >= deadline)
      return std::nullopt;
    std::this_thread::sleep_for(delay);
    delay = std::min(delay * 2, std::chrono::milliseconds(50));
  }
}
int Child::stop(std::chrono::milliseconds grace) {
  terminate();
  if (auto code = wait({}, grace))
    return *code;
  kill();
  return wait().value_or(255);
}
int run(const fs::path &executable, const std::vector<std::string> &argv,
        const SpawnOptions &options, std::stop_token stop,
        std::optional<std::chrono::milliseconds> timeout) {
  Child child;
  child.spawn(executable, argv, options);
  if (auto code = child.wait(stop, timeout))
    return *code;
  child.stop(2s);
  if (stop.stop_requested())
    throw std::runtime_error("Stopped before the game started.");
  throw std::runtime_error(paths::utf8(executable.filename()) + " did not finish in time.");
}
} // namespace riftlift::runtime
