// Windows: no Proton; RiftLiftLauncher.exe runs the game against the
// headset's own Windows OpenXR/OpenVR runtime (windows.py).
#include "common/digest.h"
#include "common/log.h"
#include "common/process.h"
#include "common/rift/detection.h"
#include "runtime/archive.h"
#include "runtime/files.h"
#include "runtime/launch.h"
#include "runtime/layout.h"
#include "runtime/rules.h"
#include "runtime/xr.h"
#include <algorithm>
#include <windows.h>

namespace riftlift::runtime {
using namespace std::chrono_literals;
namespace {
// The pinned source payload; packaged builds ship it beside the runtime.
constexpr std::string_view payload_release = "v0.10.2.2";
constexpr std::string_view payload_sha256 = "90f9b1b5b26ba85a25ad2dcb3707b7a17540b0d40d310148dd98fa76c3a619eb";
constexpr std::string_view sdk_runtime_sha256 = "f6941275692026b18666bb856d71fe1b19462017b2b2e556fe8df82461f493f5";
constexpr std::string_view meta_runtime_url = "https://securecdn.oculus.com/binaries/download/?id=3766757683456363";
constexpr std::string_view meta_runtime_sha256 = "adbdc5f0285a2ac2ead6fdd34522de98de1bf6782017d9857ea4044b2d2fd009";
const std::vector<std::string> payload_files{"RiftLiftLauncher.exe", "RiftLiftOpenXR64.dll", "RiftLiftOpenVR64.dll",
                                             "openvr_api64.dll",     "LICENSE",              "RIFTLIFT-LICENSE"};
const std::vector<std::string> platform_files{"LibOVRPlatform64_1.dll", "LibOVRPlatformImpl64_1.dll",
                                              "LibOVRPlatformImpl64_1_real.dll", "LibOVRP2P64_1.dll",
                                              "LibOVRRT64_1.dll"};
// OVRPlugin talks OpenXR directly and refuses runtimes that do not identify
// as Oculus; the pinned source payload predates this layer.
constexpr std::string_view openxr_layer_file = "RiftLiftOpenXRLayer.dll";
constexpr std::string_view openxr_layer_name = "XR_APILAYER_RIFTLIFT_ovr_compat";

bool is_file(const fs::path &file) {
  std::error_code ec;
  return fs::is_regular_file(file, ec);
}
bool all_present(const fs::path &directory, const std::vector<std::string> &names) {
  return std::ranges::all_of(names, [&](auto &n) { return is_file(directory / paths::from_utf8(n)); });
}
std::string native(fs::path path) { return paths::utf8(path.make_preferred()); }
std::string sha256_bytes(std::string_view bytes) {
  return digest::sha256({reinterpret_cast<const std::uint8_t *>(bytes.data()), bytes.size()});
}
fs::path local_app_data(const Env &env) {
  auto value = get(env, "LOCALAPPDATA");
  if (value.empty())
    throw std::runtime_error("LOCALAPPDATA is not set.");
  return paths::from_utf8(value);
}
// Meta's checksum-verified runtime package (cached as oculus-runtime.zip).
fs::path meta_package(const Layout &layout, std::stop_token stop) {
  return fetch(std::string(meta_runtime_url), layout.cache / "oculus-runtime.zip", meta_runtime_sha256,
               Origin::meta, stop);
}

struct Native {
  fs::path directory;
  bool packaged = false;
};
Native runtime_dir(const Layout &layout, const Env &env) {
  if (auto value = get(env, "RIFTLIFT_WINDOWS_RUNTIME"); !value.empty())
    return {fs::weakly_canonical(paths::from_utf8(value)), false};
  // A packaged RiftLift ships the payload in "native" beside its programs.
  if (auto bundled = paths::executable().parent_path() / "native"; is_file(bundled / "RiftLiftLauncher.exe"))
    return {bundled, true};
  return {layout.tools / "windows-native" / payload_release, false};
}

// Meta's signed discovery DLL, intact, for the static Oculus SDK loader.
fs::path install_sdk_runtime(const Layout &layout, const fs::path &native_dir, std::stop_token stop) {
  if (try_sha256_file(native_dir / "LibOVRRT64_1.dll") == sdk_runtime_sha256)
    return native_dir;
  auto directory = layout.tools / "meta-runtime";
  auto target = directory / "LibOVRRT64_1.dll";
  if (try_sha256_file(target) == sdk_runtime_sha256)
    return directory;
  auto payload = zip_entry(meta_package(layout, stop), "LibOVRRT64_1.dll");
  if (sha256_bytes(payload) != sdk_runtime_sha256)
    throw std::runtime_error("Meta SDK runtime checksum mismatch");
  write_file(target, payload);
  return directory;
}
// Completes the pinned bridge with Meta's original DLLs; never replaces the
// compatibility shim with Meta's implementation of the same name.
void install_platform_dependencies(const Layout &layout, const fs::path &target, std::stop_token stop) {
  const std::vector<std::pair<std::string, std::string>> dependencies{
      {"LibOVRPlatform64_1.dll", "LibOVRPlatform64_1.dll"},
      {"LibOVRP2P64_1.dll", "LibOVRP2P64_1.dll"},
      {"LibOVRRT64_1.dll", "LibOVRRT64_1.dll"},
      {"LibOVRPlatformImpl64_1_real.dll", "LibOVRPlatformImpl64_1.dll"}};
  std::vector<std::pair<std::string, std::string>> missing;
  for (auto &d : dependencies)
    if (!is_file(target / d.first))
      missing.push_back(d);
  if (missing.empty())
    return;
  auto package = meta_package(layout, stop);
  std::vector<std::pair<std::string, std::string>> selected;
  for (auto &[name, source] : missing)
    selected.emplace_back(name, zip_entry(package, source));
  if (sha256_bytes(zip_entry(package, "LibOVRRT64_1.dll")) != sdk_runtime_sha256)
    throw std::runtime_error("Meta SDK runtime checksum mismatch");
  for (auto &[name, bytes] : selected)
    write_file(target / name, bytes);
}
fs::path install_payload(const Layout &layout, const Native &native_dir, std::stop_token stop) {
  const auto &target = native_dir.directory;
  if (native_dir.packaged) {
    if (!all_present(target, payload_files) || !all_present(target, platform_files))
      throw std::runtime_error("RiftLift's installation is incomplete. Please reinstall RiftLift.");
    return target;
  }
  if (all_present(target, payload_files) && all_present(target, platform_files))
    return target;
  log::info("runtime", "Installing the RiftLift native payload {}...", payload_release);
  auto archive = fetch(std::format("https://github.com/Villagers654/RiftLift/releases/download/{}/riftlift-compat.zip",
                                   payload_release),
                       layout.cache / std::format("riftlift-compat-{}.zip", payload_release), payload_sha256,
                       Origin::release, stop);
  // Validate the complete archive before writing anything.
  Staging staging(layout.cache, ".windows-native-unpack-");
  extract_zip(archive, staging.path());
  if (!all_present(staging.path(), payload_files))
    throw std::runtime_error("Native payload is missing required files");
  std::error_code ec;
  for (const auto &entry : fs::recursive_directory_iterator(staging.path(), ec)) {
    if (!entry.is_regular_file(ec))
      continue;
    auto relative = entry.path().lexically_relative(staging.path()).generic_string();
    const bool wanted = std::ranges::find(payload_files, relative) != payload_files.end() ||
                        std::ranges::find(platform_files, relative) != platform_files.end() ||
                        (relative.starts_with("Input/") && relative.ends_with(".json"));
    if (wanted)
      write_file(target / paths::from_utf8(relative), read_file(entry.path()));
  }
  fs::remove(target / "local-build.json", ec);
  install_platform_dependencies(layout, target, stop);
  std::vector<std::string> missing;
  for (auto &name : platform_files)
    if (!is_file(target / name))
      missing.push_back(name);
  if (!missing.empty()) {
    std::string list;
    for (auto &m : missing)
      list += (list.empty() ? "" : ", ") + m;
    throw std::runtime_error("Native bridge installed, but Meta platform compatibility is incomplete: " + list +
                             ". Use a complete Windows package or a validated source-built runtime.");
  }
  return target;
}

std::optional<fs::path> active_openxr(const Env &env) {
  if (auto value = get(env, "XR_RUNTIME_JSON"); !value.empty())
    return paths::from_utf8(value);
  wchar_t value[32768];
  DWORD size = sizeof value;
  if (RegGetValueW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Khronos\\OpenXR\\1", L"ActiveRuntime",
                   RRF_RT_REG_SZ | RRF_SUBKEY_WOW6464KEY, nullptr, value, &size) != ERROR_SUCCESS)
    return std::nullopt;
  return fs::path(value);
}
std::optional<fs::path> active_openvr(const Env &env) {
  auto config = get(env, "VR_PATHREG_OVERRIDE");
  fs::path file = config.empty() ? local_app_data(env) / "openvr/openvrpaths.vrpath" : paths::from_utf8(config);
  try {
    for (auto &root : parse_json(read_file(file)).at("runtime")) {
      auto candidate = paths::from_utf8(root.get<std::string>());
      if (is_file(candidate / "bin/vrclient_x64.dll") || is_file(candidate / "bin/win64/vrclient_x64.dll"))
        return candidate;
    }
  } catch (...) {
  }
  return std::nullopt;
}
std::optional<fs::path> steamvr_openxr_manifest(const Env &env) {
  auto vr = active_openvr(env);
  if (!vr || !is_file(*vr / "steamxr_win64.json"))
    return std::nullopt;
  return *vr / "steamxr_win64.json";
}
bool runtime_ready(std::string_view backend, const Env &env) {
  std::error_code ec;
  if (backend == "openxr") {
    auto target = active_openxr(env);
    return target && fs::is_regular_file(*target, ec);
  }
  auto target = active_openvr(env);
  return target && fs::is_directory(*target, ec);
}
std::string select_windows_backend(const Env &env) {
  if (auto value = lower(strip(get(env, "RIFTLIFT_RUNTIME_BACKEND"))); !value.empty()) {
    if (value != "openxr" && value != "openvr")
      throw std::runtime_error("RIFTLIFT_RUNTIME_BACKEND must be 'openxr' or 'openvr'");
    return value;
  }
  if (runtime_ready("openxr", env)) {
    // SteamVR's OpenXR path rejects Oculus cube and mipmapped textures: use
    // its OpenVR interface when both registrations are SteamVR.
    auto xr = active_openxr(env), vr = active_openvr(env);
    std::error_code ec;
    if (xr && vr && fs::weakly_canonical(*xr, ec).parent_path() == fs::weakly_canonical(*vr, ec))
      return "openvr";
    return "openxr";
  }
  return "openvr";
}
// Per-process OpenXR settings; never touches the registry.
void openxr_environment(const Layout &layout, const fs::path &native_dir, Env &env) {
  std::map<std::string, std::string> updates;
  // SteamVR users often never press "Set as OpenXR runtime"; OVRPlugin titles
  // go straight to OpenXR.
  if (!env.contains("XR_RUNTIME_JSON") && !active_openxr(env))
    if (auto steamvr = steamvr_openxr_manifest(env))
      updates["XR_RUNTIME_JSON"] = native(*steamvr);
  auto library = native_dir / openxr_layer_file;
  if (is_file(library)) {
    auto directory = layout.cache / "openxr-layer";
    auto manifest = directory / "riftlift-openxr-layer.json";
    nlohmann::ordered_json layer{{"name", openxr_layer_name},
                                 // Older loaders bundled in games mis-resolve relative paths.
                                 {"library_path", native(library)},
                                 {"api_version", "1.0"},
                                 {"implementation_version", "1"},
                                 {"description", "RiftLift OVRPlugin compatibility"}};
    auto payload = nlohmann::ordered_json{{"file_format_version", "1.0.0"}, {"api_layer", layer}}.dump(2) + "\n";
    std::string current;
    try {
      current = read_file(manifest);
    } catch (...) {
    }
    if (current != payload)
      write_file(manifest, payload);
    for (auto &[name, value] : {std::pair<std::string, std::string>{"XR_API_LAYER_PATH", native(directory)},
                                {"XR_ENABLE_API_LAYERS", std::string(openxr_layer_name)}}) {
      auto existing = get(env, name);
      updates[name] = existing.empty() ? value : value + ";" + existing;
    }
  }
  for (auto &[k, v] : updates)
    env[k] = v;
}
// SteamVR app identity, so its dashboard shows the game rather than its .exe.
fs::path steamvr_manifest(const Layout &layout, const rift::Game &game) {
  nlohmann::ordered_json application{{"app_key", "riftlift.app." + game.app_key},
                                     {"launch_type", "binary"},
                                     {"binary_path_windows", native(game.executable_path())},
                                     {"strings", {{"en_us", {{"name", game.name}}}}}};
  for (auto name : {"grid", "hero", "portrait"})
    if (auto it = game.artwork.find(name); it != game.artwork.end() && is_file(paths::from_utf8(it->second))) {
      application["image_path"] = it->second;
      break;
    }
  auto manifest = layout.cache / "steamvr" / (game.slug + ".vrmanifest");
  auto payload =
      nlohmann::ordered_json{{"source", "user"}, {"applications", nlohmann::ordered_json::array({application})}}
          .dump(2) + "\n";
  std::string current;
  try {
    current = read_file(manifest);
  } catch (...) {
  }
  if (current != payload)
    write_file(manifest, payload);
  return manifest;
}
// The pinned source payload's launcher would treat /manifest as the game.
bool launcher_accepts_manifest(const fs::path &launcher) {
  const std::string needle{"/\0m\0a\0n\0i\0f\0e\0s\0t\0", 18};
  try {
    return read_file(launcher).find(needle) != std::string::npos;
  } catch (...) {
    return false;
  }
}
} // namespace

int launch(const LaunchRequest &request) {
  const auto &game = request.game;
  const auto layout = runtime::layout(request.root);
  const auto stop = request.stop;
  const auto native_dir = runtime_dir(layout, request.env);
  const auto backend = select_windows_backend(request.env);
  const std::vector<std::string> required =
      backend == "openxr" ? std::vector<std::string>{"RiftLiftLauncher.exe", "RiftLiftOpenXR64.dll"}
                          : std::vector<std::string>{"RiftLiftLauncher.exe", "RiftLiftOpenVR64.dll", "openvr_api64.dll"};
  if (!all_present(native_dir.directory, required) || (game.platform_shim && !all_present(native_dir.directory, platform_files)))
    install_payload(layout, native_dir, stop);
  if (!all_present(native_dir.directory, required))
    throw std::runtime_error("RiftLift's native payload is missing; reinstall RiftLift.");
  std::error_code ec;
  auto executable = fs::weakly_canonical(game.executable_path(), ec);
  auto root = fs::weakly_canonical(game.directory, ec);
  if (!paths::within(root, executable) || !rift::is_pe64(executable))
    throw std::runtime_error("Game executable must be an existing x64 PE inside its game folder");
  auto launcher = native_dir.directory / "RiftLiftLauncher.exe";
  std::vector<std::string> command{native(launcher), "/" + backend, "/wait", "/app", game.app_key, "/cwd",
                                   native(game.directory)};
  if (backend == "openvr" && launcher_accepts_manifest(launcher))
    command.insert(command.end(), {"/manifest", native(steamvr_manifest(layout, game))});
  command.push_back(native(executable));
  command.insert(command.end(), game.arguments.begin(), game.arguments.end());
  command.insert(command.end(), game.launch_options.begin(), game.launch_options.end());

  // openxr_environment points the game at SteamVR's OpenXR runtime when no
  // other is registered, so that fallback counts as ready too.
  if (!runtime_ready(backend, request.env) && !(backend == "openxr" && steamvr_openxr_manifest(request.env)))
    throw std::runtime_error("Configure a Windows " + backend + " runtime and connect the headset first");
  auto env = request.env;
  if (backend == "openvr")
    // Without its action manifest SteamVR falls back to legacy input.
    env["RIFTLIFT_ACTION_MANIFEST"] = native(native_dir.directory / "Input/action_manifest.json");
  for (auto &[k, v] : game.environment)
    env[k] = v;
  // Older Platform SDK loaders concatenate the DLL name directly to this value.
  env["LIBOVR_DLL_DIR"] = native(install_sdk_runtime(layout, native_dir.directory, stop)) + "\\";
  if (game.platform_shim) {
    if (!all_present(native_dir.directory, platform_files))
      throw std::runtime_error("RiftLift native platform compatibility DLL is missing");
    auto path = get(env, "PATH");
    env["PATH"] = native(native_dir.directory) + (path.empty() ? "" : ";" + path);
    env["RIFTLIFT_PLATFORM_DLL"] = native(native_dir.directory / "LibOVRPlatformImpl64_1.dll");
    env["LIBOVR_DLL_DIR"] = native(native_dir.directory) + "\\";
    // The shim's fallback ID is too small for engines that validate Oculus IDs.
    env["RIFTLIFT_USER_ID"] = platform_user_id(layout, request.env);
    if (game.platform_offline)
      env["RIFTLIFT_PLATFORM_OFFLINE"] = "1";
  }
  openxr_environment(layout, native_dir.directory, env);
  if (debug_logging_enabled(layout))
    env["RIFTLIFT_RUNTIME_TRACE"] = "1";

  std::wstring line;
  for (auto &a : command)
    line += (line.empty() ? L"" : L" ") + quote_windows_arg(paths::from_utf8(a).wstring());
  log::info("runtime", "Launching {} through Windows {}.", game.name, backend);
  log::info("runtime", "Command: {}", paths::utf8(fs::path(line)));
  Child child;
  SpawnOptions options;
  options.cwd = game.directory;
  options.env = &env;
  options.own_group = true;
  int code = 1;
  if (!stop.stop_requested()) {
    child.spawn(launcher, command, options);
    request.started();
    if (auto exit = child.wait(stop))
      code = *exit;
    else
      code = child.stop(5s);
  }
  log::info("runtime", "Exit code: {} (0x{:08X})", code, std::uint32_t(code));
  try {
    auto evidence = read_file(local_app_data(request.env) / "RiftLift/RiftLiftLauncher.txt");
    std::string_view rest = evidence;
    while (!rest.empty()) {
      auto end = rest.find('\n');
      auto text = rest.substr(0, end);
      rest = end == rest.npos ? std::string_view{} : rest.substr(end + 1);
      if (text.ends_with('\r'))
        text.remove_suffix(1);
      if (!text.empty())
        log::info("launcher", "{}", text);
    }
  } catch (...) {
  }
  return code;
}
} // namespace riftlift::runtime
