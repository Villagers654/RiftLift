// Linux: a game runs through GE-Proton in RiftLift's private prefix
// (launch.py _prepare_launch and launch).
#include "common/log.h"
#include "common/rift/detection.h"
#include "runtime/diagnostics.h"
#include "runtime/files.h"
#include "runtime/launch.h"
#include "runtime/proton.h"
#include "runtime/rules.h"
#include "runtime/xr.h"
#include <csignal>
#include <cstdio>
#include <dlfcn.h>
#include <fcntl.h>
#include <random>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>

namespace riftlift::runtime {
using namespace std::chrono_literals;
namespace {
std::string read_small(const fs::path &file) {
  try {
    return read_file(file);
  } catch (...) {
    return {};
  }
}
// A live process of this user whose name is exactly `name`.
bool same_user_process(const fs::path &proc_entry, std::string_view name) {
  struct stat info{};
  if (stat(proc_entry.c_str(), &info) || info.st_uid != getuid())
    return false;
  return strip(read_small(proc_entry / "comm")) == name;
}
// steam.py _steam_client_ready: Steam's recorded main process (or any
// same-user main process); steamwebhelper alone is not enough.
bool steam_client_ready() {
  auto home = home_dir();
  for (auto relative : {".steam/steam.pid", ".local/share/Steam/steam.pid",
                        ".var/app/com.valvesoftware.Steam/.steam/steam.pid",
                        ".var/app/com.valvesoftware.Steam/data/Steam/steam.pid"}) {
    auto pid = strip(read_small(home / relative));
    if (!pid.empty() && pid.find_first_not_of("0123456789") == pid.npos &&
        same_user_process(fs::path("/proc") / pid, "steam"))
      return true;
  }
  std::error_code ec;
  for (const auto &entry : fs::directory_iterator("/proc", ec)) {
    auto name = entry.path().filename().string();
    if (name.find_first_not_of("0123456789") == name.npos && same_user_process(entry.path(), "steam"))
      return true;
  }
  return false;
}
// Some Steamworks titles restart through a closed Steam client, which would
// relaunch a plain copy without RiftLift's XR environment.
void ensure_steam_running(const Installer &installer, std::stop_token stop) {
  if (steam_client_ready())
    return;
  fs::path steam;
  try {
    if (auto script = installer.steam_root() / "steam.sh"; access(script.c_str(), X_OK) == 0)
      steam = script;
  } catch (...) {
  }
  if (steam.empty())
    steam = find_program("steam", installer.base());
  if (steam.empty())
    throw std::runtime_error("Steam is not running and its launcher is not on PATH; start Steam and retry");
  log::info("runtime", "Starting Steam before the Steamworks game...");
  Child client;
  SpawnOptions options;
  options.own_group = true;
  options.quiet = true;
  client.spawn(steam, {paths::utf8(steam), "-silent"}, options);
  client.detach();
  const auto deadline = std::chrono::steady_clock::now() + 30s;
  while (std::chrono::steady_clock::now() < deadline) {
    if (steam_client_ready()) {
      // Steam publishes its PID before it finishes restoring its session.
      auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - std::chrono::steady_clock::now());
      if (!sleep_for(std::min<std::chrono::milliseconds>(8s, std::max(left, 0ms)), stop))
        throw std::runtime_error("Stopped before the game started.");
      if (steam_client_ready())
        return;
    }
    if (!sleep_for(200ms, stop))
      throw std::runtime_error("Stopped before the game started.");
  }
  throw std::runtime_error("Steam did not become ready in time; start Steam and retry");
}
// Processes carrying this launch's RIFTLIFT_LAUNCH_ID: Wine services can
// leave Proton's process group, the marker still identifies them.
std::vector<pid_t> marked_processes(const std::string &launch_id) {
  const auto marker = "RIFTLIFT_LAUNCH_ID=" + launch_id;
  std::vector<pid_t> result;
  std::error_code ec;
  for (const auto &entry : fs::directory_iterator("/proc", ec)) {
    auto name = entry.path().filename().string();
    if (name.empty() || name.find_first_not_of("0123456789") != name.npos)
      continue;
    auto environ_text = read_small(entry.path() / "environ");
    std::string_view rest = environ_text;
    while (!rest.empty()) {
      auto end = rest.find('\0');
      if (rest.substr(0, end) == marker) {
        result.push_back(pid_t(std::stol(name)));
        break;
      }
      rest = end == rest.npos ? std::string_view{} : rest.substr(end + 1);
    }
  }
  return result;
}
void terminate_marked_processes(const std::string &launch_id) {
  auto remaining = marked_processes(launch_id);
  for (auto pid : remaining)
    ::kill(pid, SIGTERM);
  const auto deadline = std::chrono::steady_clock::now() + 5s;
  while (!remaining.empty() && std::chrono::steady_clock::now() < deadline) {
    std::this_thread::sleep_for(50ms);
    remaining = marked_processes(launch_id);
  }
  for (auto pid : remaining)
    ::kill(pid, SIGKILL);
}
// steam_appid.txt keeps SteamAPI_RestartAppIfNecessary from replacing the
// prepared process; created for the launch only, never replacing the game's.
class SteamAppIdMarker {
public:
  SteamAppIdMarker(const rift::Game &game) {
    if (game.source != "steam" || !game.steam_app_id)
      return;
    file_ = game.directory / "steam_appid.txt";
    int fd = ::open(file_.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
    if (fd < 0) {
      if (errno == EEXIST)
        return;
      throw std::runtime_error("cannot prepare Steamworks launch marker " + paths::utf8(file_) + ": " +
                               std::generic_category().message(errno));
    }
    auto text = std::to_string(game.steam_app_id) + "\n";
    [[maybe_unused]] auto written = ::write(fd, text.data(), text.size());
    struct stat info{};
    if (fstat(fd, &info) == 0)
      identity_ = {info.st_dev, info.st_ino};
    ::close(fd);
  }
  ~SteamAppIdMarker() {
    struct stat info{};
    if (identity_ && stat(file_.c_str(), &info) == 0 && std::pair{info.st_dev, info.st_ino} == *identity_)
      ::unlink(file_.c_str());
  }

private:
  fs::path file_;
  std::optional<std::pair<dev_t, ino_t>> identity_;
};
std::string random_id() {
  std::random_device device;
  return std::format("{:08x}{:08x}{:08x}{:08x}", device(), device(), device(), device());
}
} // namespace

int check_components(const fs::path &root, const Env &env) {
  Installer installer(layout(root), env, {});
  for (auto &line : installer.check())
    std::printf("%s\n", line.c_str());
  return 0;
}
int probe_openvr(const std::string &library) {
  void *handle = dlopen(library.c_str(), RTLD_NOW | RTLD_LOCAL);
  if (!handle) {
    std::fprintf(stderr, "%s\n", dlerror());
    return 1;
  }
  for (auto symbol : {"HmdSystemFactory", "VRClientCoreFactory"})
    if (!dlsym(handle, symbol)) {
      std::fprintf(stderr, "%s: undefined symbol %s\n", library.c_str(), symbol);
      return 1;
    }
  return 0;
}

int launch(const LaunchRequest &request) {
  auto game = request.game;
  auto stop = request.stop;
  const auto layout = runtime::layout(request.root);
  // Records saved before nested Unreal layouts were detected name the
  // bootstrapper, whose child would run without the runtime.
  if (auto shipping = rift::unreal_shipping_for_bootstrap(game.executable_path())) {
    game.executable = shipping->lexically_relative(game.directory).generic_string();
    if (!std::ranges::any_of(game.arguments, [](auto &a) {
          auto v = lower(a);
          return v == "-vr" || v == "-oculus" || v == "-openxr" || v == "-steamvr";
        }))
      game.arguments.push_back("-vr");
    log::info("runtime", "Launching the Unreal shipping binary {}.", game.executable);
  }
  const auto executable = game.executable_path();
  std::error_code ec;
  if (!fs::is_regular_file(executable, ec))
    throw std::runtime_error("game executable is missing: " + paths::utf8(executable));
  Installer installer(layout, request.env, stop);
  if (game.source == "steam")
    ensure_steam_running(installer, stop);
  const auto rift_runtime = installer.install_rift_runtime();
  const auto proton_root = installer.install_proton();

  std::set<std::string> capabilities;
  if (rift::uses_openvr_runtime(game.directory)) capabilities.insert("openvr");
  if (rift::uses_unity_oculus_plugin(game.directory)) capabilities.insert("unity-oculus-plugin");
  if (rift::uses_d3d12_runtime(executable)) capabilities.insert("d3d12");
  const bool unity = rift::is_unity_player(executable), unreal = rift::is_unreal_shipping(executable);
  if (unity) capabilities.insert("unity");
  if (unreal) capabilities.insert("unreal");
  const auto backend = select_backend(get(request.env, "RIFTLIFT_RUNTIME_BACKEND"), game.arguments, capabilities);
  const auto bridge = installer.native_xr_bridge(backend);

  auto game_arguments = game.arguments;
  game_arguments.insert(game_arguments.end(), game.launch_options.begin(), game.launch_options.end());
  game_arguments = oculus_launch_arguments(unreal ? Engine::unreal : unity ? Engine::unity : Engine::other,
                                           std::move(game_arguments));
  std::vector<std::string> arguments{paths::utf8(proton_root / "proton"), "run"};
  for (auto &a : launcher_arguments(paths::utf8(rift_runtime / "RiftLiftLauncher.exe"), backend, game.app_key,
                                    linux_to_windows(game.directory), linux_to_windows(executable),
                                    game_arguments))
    arguments.push_back(a);

  std::optional<OpenVrSelection> openvr;
  if (backend == "openvr")
    openvr = installer.select_openvr_runtime();
  auto env = installer.launch_environment(game.directory, game.platform_shim,
                                          game.platform_offline || game.source == "meta",
                                          active_runtime_json(request.env));
  if (openvr)
    disable_openxr_for_direct_openvr(env, openvr->kind);
  configure_proton_identity(env, game.source, game.steam_app_id);
  if (!openvr) {
    env["DXVK_NO_VR"] = "1";
    // Proton reads this before the game starts; an empty registry keeps it
    // from also initializing an OpenVR client.
    env["VR_PATHREG_OVERRIDE"] = "/dev/null";
  } else {
    installer.clear_proton_openvr_cache();
    configure_openvr_environment(env, layout.config, rift_runtime, openvr->runtime, openvr->registry, openvr->kind);
  }
  for (auto &[key, value] : game.environment)
    if (key != "WINEDLLOVERRIDES")
      env[key] = value;
  if (auto it = game.environment.find("WINEDLLOVERRIDES"); it != game.environment.end())
    apply_dll_overrides(env, it->second);
  apply_dll_overrides(env, game.dll_overrides);
  configure_mod_loaders(env, executable);
  const bool debug = get(env, "PROTON_LOG") == "1";
  if (debug) {
    env["RIFTLIFT_RUNTIME_TRACE"] = "1";
    clear_runtime_traces(layout);
  }
  std::vector<std::string> wrapper;
  fs::path program = proton_root / "proton";
  if (auto value = strip(get(request.env, "RIFTLIFT_LAUNCH_WRAPPER")); !value.empty()) {
    wrapper = shell_split(value);
    if (wrapper.empty() || (program = find_program(wrapper[0], request.env)).empty())
      throw std::runtime_error("configured launch wrapper was not found: " + value);
  }
  // A Wine server left running by this run's prefix maintenance can stall
  // Proton's first `run` in umu.exe; start the game on an idle prefix.
  if (installer.prefix_changed())
    installer.shutdown_compat_prefix();

  const auto launch_id = random_id();
  env["RIFTLIFT_LAUNCH_ID"] = launch_id;
  log::info("runtime", "Launching {} through RiftLift native {} runtime -> headset...", game.name,
            backend == "openxr" ? "OPENXR" : "OPENVR");
  log::info("runtime", "Native XR bridge: {} (Wine unixlib)", bridge);
  if (openvr)
    log::info("runtime", "OpenVR runtime: {} ({})", paths::utf8(openvr->runtime), openvr->kind);
  std::string command;
  for (auto &a : arguments)
    command += (command.empty() ? "" : " ") + a;
  log::debug("runtime", "Command: {}", command);

  SteamAppIdMarker appid(game);
  // Unity's OculusXRPlugin links LibOVRRT directly and needs Meta's runtime
  // service resident; OVRServiceLauncher exits at once under Wine.
  Child service;
  auto service_exe = layout.prefix / "pfx/drive_c/Program Files/Oculus/Support/oculus-runtime/OVRServer_x64.exe";
  if (capabilities.contains("unity-oculus-plugin") && fs::is_regular_file(service_exe, ec)) {
    SpawnOptions options;
    options.cwd = service_exe.parent_path();
    options.env = &env;
    options.own_group = true;
    options.quiet = true;
    service.spawn(proton_root / "proton",
                  {paths::utf8(proton_root / "proton"), "run", paths::utf8(service_exe)}, options);
    sleep_for(1500ms, stop);
  }
  std::jthread maintenance;
  if (debug)
    maintenance = std::jthread([&layout](std::stop_token token) {
      while (sleep_for(1s, token)) {
        trim_runtime_traces(layout);
        // Only append-mode streams are safe to compact while writers run.
        prepare_proton_logs(layout);
      }
    });

  std::vector<std::string> argv = wrapper;
  argv.insert(argv.end(), arguments.begin(), arguments.end());
  Child child;
  SpawnOptions options;
  options.cwd = game.directory;
  options.env = &env;
  options.own_group = true;
  int code = 0;
  bool stopped = false;
  if (!stop.stop_requested()) {
    child.spawn(program, argv, options);
    request.started();
    log::info("runtime", "Game process started (pid {}).", child.pid());
    if (auto exit = child.wait(stop)) {
      code = *exit;
    } else {
      stopped = true;
      log::info("runtime", "Stopping {}...", game.name);
      code = child.stop(5s);
    }
  } else {
    stopped = true;
  }
  terminate_marked_processes(launch_id);
  if (service.running())
    service.stop(5s);
  if (stopped) {
    try {
      installer.shutdown_compat_prefix();
    } catch (const std::exception &e) {
      log::warn("runtime", "{}", e.what());
    }
  }
  if (maintenance.joinable()) {
    maintenance.request_stop();
    maintenance.join();
  }
  trim_runtime_traces(layout);
  log::info("runtime", "{} exited with code {}.", game.name, code);
  return code;
}
} // namespace riftlift::runtime
