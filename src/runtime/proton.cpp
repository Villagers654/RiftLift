#include "runtime/proton.h"
#include "common/log.h"
#include "common/process.h"
#include "runtime/archive.h"
#include "runtime/diagnostics.h"
#include "runtime/files.h"
#include "runtime/meta_runtime.h"
#include "runtime/rules.h"
#include "runtime/xr.h"
#include <algorithm>
#include <fstream>
#include <thread>

namespace riftlift::runtime {
using namespace std::chrono_literals;
namespace {
constexpr std::string_view proton_sha256 = "861c2edc8d40d051fb1e7a692deb953be52bd339c46d90f2b7dde50ddad91266";
std::string proton_url() {
  return std::format("https://github.com/GloriousEggroll/proton-ge-custom/releases/download/{0}/{0}.tar.gz",
                     proton_version);
}
const std::vector<std::pair<std::string, std::string>> dxvk_files{
    {"x64/d3d11.dll", "x86_64-windows/d3d11.dll"},
    {"x64/dxgi.dll", "x86_64-windows/dxgi.dll"},
    {"x32/d3d11.dll", "i386-windows/d3d11.dll"},
    {"x32/dxgi.dll", "i386-windows/dxgi.dll"},
};
const std::vector<std::string> proton_openvr_files{"i386-windows/openvr_api_dxvk.dll",
                                                   "x86_64-windows/openvr_api_dxvk.dll"};
const std::vector<std::string> rift_runtime_files{
    "RiftLiftLauncher.exe",
    "RiftLiftOpenXRLayer.dll",
    "RiftLiftOpenXR64.dll",
    "RiftLiftOpenVR64.dll",
    "openvr_api64.dll",
    "LibOVRPlatformImpl64_1.dll",
    "Input/action_manifest.json",
    "Input/gamepad_default.json",
    "Input/holographic_controller_default.json",
    "Input/knuckles_default.json",
    "Input/oculus_touch_default.json",
    "Input/vive_controller_default.json",
    "Input/vive_cosmos_default.json",
};
const std::vector<std::string> openvr_runtime_files{"libxrizer.so", "bin/linux64/vrclient.so",
                                                    "bin/version.txt"};
constexpr std::string_view meta_runtime_windows = "C:\\Program Files\\Oculus\\Support\\oculus-runtime";

bool exists_file(const fs::path &file) {
  std::error_code ec;
  return fs::is_regular_file(file, ec);
}
bool exists_dir(const fs::path &directory) {
  std::error_code ec;
  return fs::is_directory(directory, ec);
}
bool file_hashes_match(const fs::path &directory, const Json &expected,
                       const std::vector<std::string> &required) {
  if (!expected.is_object() || required.empty())
    return false;
  for (auto &name : required) {
    const auto &value = field(expected, name);
    if (!value.is_string() || try_sha256_file(directory / name) != value.get<std::string>())
      return false;
  }
  return true;
}
bool installed_payload_current(const fs::path &directory, const std::string &version,
                               const std::vector<std::string> &required) {
  try {
    return strip(read_file(directory / ".riftlift-version")) == version &&
           file_hashes_match(directory, parse_json(read_file(directory / ".riftlift-files.json")),
                             required);
  } catch (...) {
    return false;
  }
}
void record_payload_files(const fs::path &directory, const std::vector<std::string> &required) {
  nlohmann::ordered_json hashes = nlohmann::ordered_json::object();
  for (auto &name : required)
    hashes[name] = sha256_file(directory / name);
  write_file(directory / ".riftlift-files.json", hashes.dump(2) + "\n");
}
bool contains_marker(const fs::path &file, std::string_view marker) {
  std::ifstream in(file, std::ios::binary);
  std::string tail, chunk(64 * 1024, '\0');
  while (in) {
    in.read(chunk.data(), std::streamsize(chunk.size()));
    auto payload = tail + chunk.substr(0, size_t(in.gcount()));
    if (payload.find(marker) != payload.npos)
      return true;
    tail = payload.size() >= marker.size() ? payload.substr(payload.size() - marker.size() + 1) : payload;
  }
  return false;
}
std::string magic(const fs::path &file, size_t size) {
  std::ifstream in(file, std::ios::binary);
  if (!in)
    throw std::runtime_error("missing " + paths::utf8(file));
  std::string bytes(size, '\0');
  in.read(bytes.data(), std::streamsize(size));
  bytes.resize(size_t(in.gcount()));
  return bytes;
}
// The WiVRn Flatpak's own advice for Steam games, from its recent server logs.
std::optional<std::map<std::string, std::string>> wivrn_pressure_vessel_env(const std::string &app_id) {
  std::error_code ec;
  auto directory = home_dir() / ".var/app" / app_id / ".local/state/wivrn/wivrn-dashboard";
  std::vector<std::pair<fs::file_time_type, fs::path>> logs;
  for (const auto &entry : fs::directory_iterator(directory, ec)) {
    auto name = paths::utf8(entry.path().filename());
    if (name.starts_with("server_logs_") && name.ends_with(".txt"))
      logs.emplace_back(entry.last_write_time(ec), entry.path());
  }
  std::ranges::sort(logs, std::greater{});
  for (size_t i = 0; i < logs.size() && i < 3; ++i) {
    if (fs::file_size(logs[i].second, ec) > 2 * 1024 * 1024 || ec)
      continue;
    std::string text;
    try {
      text = read_file(logs[i].second);
    } catch (...) {
      continue;
    }
    std::string_view rest = text;
    while (!rest.empty()) {
      auto start = rest.rfind('\n', rest.size() - 1);
      auto line = start == rest.npos ? rest : rest.substr(start + 1);
      rest = start == rest.npos ? std::string_view{} : rest.substr(0, start);
      if (auto pairs = parse_pressure_vessel_line(line, [](const fs::path &p) { return exists_dir(p); }))
        return pairs;
    }
  }
  return std::nullopt;
}
} // namespace

Installer::Installer(Layout layout, Env base, std::stop_token stop)
    : layout_(std::move(layout)), base_(std::move(base)), stop_(stop) {}

fs::path Installer::steam_root() const {
  if (auto root = get(base_, "RIFTLIFT_STEAM_ROOT"); !root.empty())
    return paths::from_utf8(root);  // the launcher's test override
  auto home = home_dir();
  for (auto relative : {".local/share/Steam", ".steam/steam", ".var/app/com.valvesoftware.Steam/data/Steam"})
    if (exists_dir(home / relative))
      return fs::canonical(home / relative);
  throw std::runtime_error("Steam was not found; install and start Steam once, then retry");
}
bool Installer::debug_logging() const {
  if (auto it = base_.find("RIFTLIFT_PROTON_LOG"); it != base_.end())
    return it->second != "" && it->second != "0";
  return debug_logging_enabled(layout_);
}
fs::path Installer::archive_override(const char *variable) const {
  auto value = get(base_, variable);
  if (value.empty())
    return {};
  if (value.starts_with("~/"))
    return home_dir() / paths::from_utf8(value.substr(2));
  return paths::from_utf8(value);
}

fs::path Installer::install_proton() {
  if (proton_)
    return *proton_;
  auto target = steam_root() / "compatibilitytools.d" / proton_version;
  if (!exists_file(target / "proton")) {
    log::info("runtime", "Installing {}...", proton_version);
    auto archive = fetch(proton_url(), layout_.cache / (std::string(proton_version) + ".tar.gz"),
                         proton_sha256, Origin::release, stop_);
    fs::create_directories(target.parent_path());
    Staging staging(target.parent_path(), ".proton-unpack-");
    extract_tar(archive, staging.path(), true, stop_);
    auto source = staging.path() / proton_version;
    if (!exists_file(source / "proton"))
      throw std::runtime_error("GE-Proton archive did not contain the expected launcher");
    replace_directory(source, target);
  }
  install_dxvk_compat(target);
  proton_ = target;
  return target;
}

void Installer::install_dxvk_compat(const fs::path &proton) {
  auto destination = proton / "files/lib/wine/dxvk";
  auto archive = archive_override("RIFTLIFT_DXVK_ARCHIVE");
  if (archive.empty())
    archive = fetch(release_url() + "/riftlift-dxvk.tar.gz",
                    layout_.cache / ("dxvk-" + std::string(dxvk_version) + ".tar.gz"), "",
                    Origin::release, stop_);
  const auto artifact = sha256_file(archive);
  auto openvr_present = [&](const fs::path &directory) {
    return std::ranges::all_of(proton_openvr_files, [&](auto &n) { return exists_file(directory / n); });
  };
  try {
    auto installed = parse_json(read_file(destination / ".riftlift-dxvk.json"));
    const auto &files = field(installed, "files");
    if (str(installed, "version") == dxvk_version && str(installed, "artifact_sha256") == artifact &&
        openvr_present(destination) &&
        std::ranges::all_of(dxvk_files, [&](auto &f) {
          return str(files, f.second) == try_sha256_file(destination / f.second);
        }))
      return;
  } catch (...) {
  }
  log::info("runtime", "Installing RiftLift DXVK {}...", dxvk_version);
  fs::create_directories(destination.parent_path());
  Staging staging(destination.parent_path(), ".dxvk-unpack-");
  extract_tar(archive, staging.path(), false, stop_);
  auto source = staging.path() / "dxvk";
  std::string packaged;
  try {
    packaged = strip(read_file(source / "VERSION"));
  } catch (...) {
    throw std::runtime_error("RiftLift DXVK payload is incomplete");
  }
  if (packaged != dxvk_version)
    throw std::runtime_error("RiftLift DXVK payload has unexpected version '" + packaged + "'");
  std::map<std::string, std::string> payloads, hashes;
  for (auto &[from, to] : dxvk_files) {
    std::string bytes;
    try {
      bytes = read_file(source / from);
    } catch (...) {
      throw std::runtime_error("RiftLift DXVK payload is incomplete");
    }
    if (!bytes.starts_with("MZ"))
      throw std::runtime_error("RiftLift DXVK payload is incomplete");
    hashes[to] = sha256_file(source / from);
    payloads[to] = std::move(bytes);
  }
  auto staged = staging.path() / "installed";
  constexpr auto copy_tree = fs::copy_options::recursive | fs::copy_options::copy_symlinks |
                             fs::copy_options::overwrite_existing;
  if (!openvr_present(destination)) {
    // Older installs replaced the whole directory, losing Proton's DLLs.
    auto proton_archive = fetch(proton_url(), layout_.cache / (std::string(proton_version) + ".tar.gz"),
                                proton_sha256, Origin::release, stop_);
    auto restored = staging.path() / "proton";
    extract_tar(proton_archive, restored, true, stop_);
    auto stock = restored / proton_version / "files/lib/wine/dxvk";
    if (!openvr_present(stock))
      throw std::runtime_error("GE-Proton archive is missing OpenVR DXVK DLLs");
    fs::copy(stock, staged, copy_tree);
  }
  if (fs::exists(destination))
    fs::copy(destination, staged, copy_tree);
  for (auto &[relative, bytes] : payloads)
    write_file(staged / relative, bytes, fs::perms(0644));
  Json marker{{"version", dxvk_version}, {"artifact_sha256", artifact}, {"files", hashes}};
  write_file(staged / ".riftlift-dxvk.json", marker.dump(2) + "\n");
  replace_directory(staged, destination);
}

std::string Installer::native_xr_bridge(std::string_view backend) {
  std::string pe_name, unix_name;
  if (backend == "openxr")
    pe_name = "wineopenxr.dll", unix_name = "wineopenxr.so";
  else if (backend == "openvr")
    pe_name = "vrclient_x64.dll", unix_name = "vrclient_x64.so";
  else
    throw std::runtime_error("unsupported XR backend: " + std::string(backend));
  auto wine = install_proton() / "files/lib/wine";
  auto pe = wine / "x86_64-windows" / pe_name, unix_library = wine / "x86_64-unix" / unix_name;
  std::string upper(backend);
  std::ranges::transform(upper, upper.begin(), [](unsigned char c) { return char(std::toupper(c)); });
  std::string pe_magic, unix_magic;
  for (auto &[file, size, out] : {std::tuple{pe, 2, &pe_magic}, std::tuple{unix_library, 4, &unix_magic}}) {
    try {
      *out = magic(file, size_t(size));
    } catch (...) {
      throw std::runtime_error("GE-Proton is missing its native " + upper + " bridge: " + paths::utf8(file));
    }
  }
  if (pe_magic != "MZ" || unix_magic != "\x7f" "ELF")
    throw std::runtime_error("GE-Proton's native " + upper + " bridge has an invalid binary format");
  if (!contains_marker(pe, "__wine_init_unix_call") || !contains_marker(unix_library, "__wine_unix_call_funcs"))
    throw std::runtime_error("GE-Proton's " + upper + " files are not a Wine unixlib pair");
  return pe_name + " -> " + unix_name;
}

Env Installer::proton_environment(const std::optional<fs::path> &game_dir) {
  ProtonSettings settings{layout_.prefix, steam_root(), game_dir.value_or(layout_.data), debug_logging()};
  if (settings.debug) {
    auto logs = prepare_debug_logs(layout_);
    settings.proton_logs = logs.proton;
    settings.crash_logs = logs.crashes;
    settings.graphics_logs = logs.graphics;
    settings.openvr_logs = logs.openvr;
  }
  return runtime::proton_environment(base_, settings);
}

void Installer::proton(const std::vector<std::string> &args) {
  auto executable = install_proton() / "proton";
  auto env = proton_environment();
  std::vector<std::string> argv{paths::utf8(executable)};
  argv.insert(argv.end(), args.begin(), args.end());
  prefix_changed_ = true;
  SpawnOptions options;
  options.env = &env;
  if (int code = run(executable, argv, options, stop_)) {
    std::string command;
    for (auto &a : args)
      command += " " + a;
    throw std::runtime_error(std::format("proton{} failed (exit {})", command, code));
  }
}
void Installer::registry_add(const std::string &key, const std::optional<std::string> &name,
                             const std::string &kind, const std::string &value) {
  auto args = reg_add_arguments({key, name, kind, value});
  args.insert(args.begin(), "runinprefix");
  proton(args);
}

fs::path Installer::initialize_prefix() {
  for (auto &dir : {layout_.data, layout_.cache, layout_.config, layout_.prefix, layout_.tools})
    fs::create_directories(dir);
  if (!exists_dir(layout_.prefix / "pfx/drive_c")) {
    // Prefix maintenance must bypass Proton's Steam/UMU game launcher: the
    // game verb on a clean prefix can leave steam.exe helpers waiting forever.
    log::info("runtime", "Creating the compatibility prefix...");
    proton({"runinprefix", "cmd.exe", "/c", "exit"});
  }
  auto prefix = layout_.prefix / "pfx";
  fs::create_directories(prefix /
                         "drive_c/users/steamuser/AppData/Roaming/Microsoft/Internet Explorer/Quick Launch");
  return prefix;
}

void Installer::install_meta_signing_root(const fs::path &support) {
  auto marker = support / ".riftlift-meta-signing-root-v2";
  if (exists_file(marker) && meta_signing_root_installed(layout_.prefix))
    return;
  if (!signed_meta_runtime_current(support / "oculus-runtime"))
    return;
  std::error_code ec;
  fs::remove(marker, ec);
  // Wine's certutil can silently leave the store unchanged on hosts that do
  // not already trust this legacy root; write its properties directly.
  proton({"runinprefix", "reg.exe", "add", meta_signing_root_registry_key(), "/v", "Blob", "/t",
          "REG_BINARY", "/d", meta_signing_root_blob_hex(), "/f"});
  // Only recreate the marker once Wine can read the entry back.
  proton({"runinprefix", "reg.exe", "query", meta_signing_root_registry_key(), "/v", "Blob"});
  write_file(marker, std::string(meta_signing_root_thumbprint) + "\n", fs::perms(0644));
}
void Installer::configure_meta_registry(const fs::path &support) {
  auto marker = support / ".riftlift-registry-v4";
  if (exists_file(marker))
    return;
  for (auto &value : meta_registry_values())
    registry_add(value.key, value.name, value.kind, value.value);
  write_file(marker, "1\n", fs::perms(0644));
}
fs::path Installer::install_meta_runtime() {
  auto prefix = initialize_prefix();
  auto support = prefix / "drive_c/Program Files/Oculus/Support";
  std::error_code ec;
  fs::remove_all(support / "oculus-client", ec);
  install_meta_packages(layout_, support, stop_);
  patch_meta_runtime(support / "oculus-runtime");
  install_meta_signing_root(support);
  configure_meta_registry(support);
  return support;
}

fs::path Installer::install_rift_runtime() {
  auto destination = layout_.tools / "rift-runtime";
  if (installed_payload_current(destination, runtime_version(), rift_runtime_files))
    return destination;
  log::info("runtime", "Installing the RiftLift compatibility runtime {}...", runtime_version());
  auto archive = archive_override("RIFTLIFT_RUNTIME_ARCHIVE");
  if (archive.empty())
    archive = fetch(release_url() + "/riftlift-compat.zip",
                    layout_.cache / ("riftlift-compat-" + runtime_version() + ".zip"), "",
                    Origin::release, stop_);
  fs::create_directories(layout_.tools);
  Staging staging(layout_.tools, ".rift-runtime-unpack-");
  extract_zip(archive, staging.path());
  auto nested = staging.path() / "riftlift-runtime";
  auto source = exists_dir(nested) && !exists_file(staging.path() / "RiftLiftLauncher.exe")
                    ? nested : staging.path();
  if (!std::ranges::all_of(rift_runtime_files, [&](auto &n) { return exists_file(source / n); }))
    throw std::runtime_error("RiftLift runtime payload is incomplete");
  record_payload_files(source, rift_runtime_files);
  write_file(source / ".riftlift-version", runtime_version() + "\n", fs::perms(0644));
  replace_directory(source, destination);
  return destination;
}

void Installer::validate_openvr_library(const fs::path &library) {
  // A separate process, so a broken native loader cannot take this one down.
  std::stop_source timeout;
  std::stop_callback forward(stop_, [&] { timeout.request_stop(); });
  bool timed_out = false;
  std::jthread timer([&](std::stop_token token) {
    if (sleep_for(15s, token)) {
      timed_out = true;
      timeout.request_stop();
    }
  });
  std::string error;
  int code = 0;
  try {
    code = run_tool(paths::executable(), {"--probe-openvr", paths::utf8(fs::weakly_canonical(library))},
                    [](std::string_view) {}, &error, timeout.get_token());
  } catch (const std::exception &e) {
    throw std::runtime_error(std::string("Could not validate the XRizer library: ") + e.what());
  }
  timer.request_stop();
  timer.join();
  if (stop_.stop_requested())
    throw std::runtime_error("Stopped before the game started.");
  if (timed_out)
    throw std::runtime_error("Could not validate the XRizer library: the check timed out");
  if (code) {
    auto detail = strip(error);
    if (detail.size() > 2000)
      detail = detail.substr(detail.size() - 2000);
    throw std::runtime_error("XRizer cannot load on this system: " +
                             (detail.empty() ? std::format("loader exited {}", code) : detail));
  }
}

fs::path Installer::install_openvr_runtime() {
  auto destination = layout_.tools / "openvr-runtime";
  if (installed_payload_current(destination, runtime_version(), openvr_runtime_files)) {
    validate_openvr_library(destination / "libxrizer.so");
    write_openvr_path_registry(destination);
    return destination;
  }
  log::info("runtime", "Installing XRizer {}...", runtime_version());
  auto archive = archive_override("RIFTLIFT_OPENVR_RUNTIME_ARCHIVE");
  if (archive.empty())
    archive = fetch(release_url() + "/riftlift-xrizer.tar.gz",
                    layout_.cache / ("openvr-runtime-" + runtime_version() + ".tar.gz"), "",
                    Origin::release, stop_);
  fs::create_directories(layout_.tools);
  {
    Staging staging(layout_.tools, ".openvr-runtime-unpack-");
    extract_tar(archive, staging.path(), false, stop_);
    auto nested = staging.path() / "xrizer";
    auto source = exists_dir(nested) ? nested : staging.path();
    auto library = source / "libxrizer.so";
    if (!exists_file(library))
      throw std::runtime_error("RiftLift OpenVR runtime payload is incomplete");
    validate_openvr_library(library);
    auto proton_library = source / "bin/linux64/vrclient.so";
    fs::create_directories(proton_library.parent_path());
    std::error_code ec;
    fs::create_hard_link(library, proton_library, ec);
    if (ec)
      copy_preserving(library, proton_library);
    write_file(source / "bin/version.txt", runtime_version() + "\n", fs::perms(0644));
    record_payload_files(source, openvr_runtime_files);
    write_file(source / ".riftlift-version", runtime_version() + "\n", fs::perms(0644));
    replace_directory(source, destination);
  }
  write_openvr_path_registry(destination);
  return destination;
}

fs::path Installer::write_openvr_path_registry(const fs::path &runtime) {
  auto target = layout_.config / "openvr/openvrpaths.vrpath";
  auto steam_install = runtime.parent_path().parent_path().parent_path();
  fs::path config, logs;
  if (exists_file(runtime / "steamxr_linux64.json") && exists_dir(steam_install / "config")) {
    // SteamVR's settings and logs belong to Steam; the registry file itself
    // stays private, so selecting SteamVR never edits the user's global one.
    config = steam_install / "config";
    logs = steam_install / "logs";
  } else {
    config = layout_.config / "openvr/runtime";
    logs = prepare_debug_logs(layout_).openvr;
  }
  for (auto &dir : {target.parent_path(), config, logs})
    fs::create_directories(dir);
  nlohmann::ordered_json payload{{"version", 1},
                                 {"runtime", {paths::utf8(runtime)}},
                                 {"config", {paths::utf8(config)}},
                                 {"log", {paths::utf8(logs)}}};
  write_file(target, payload.dump(2) + "\n");
  return target;
}
fs::path Installer::private_openvr_path_registry(const fs::path &source) {
  auto target = layout_.config / "openvr/openvrpaths.vrpath";
  if (source == fs::weakly_canonical(target))
    return target;
  nlohmann::ordered_json payload;
  try {
    payload = nlohmann::ordered_json::parse(read_file(source));
  } catch (...) {
    throw std::runtime_error("OpenVR path registry is not readable: " + paths::utf8(source));
  }
  if (!payload.is_object())
    throw std::runtime_error("OpenVR path registry is invalid: " + paths::utf8(source));
  write_file(target, payload.dump(2) + "\n");
  return target;
}
OpenVrSelection Installer::select_openvr_runtime(const std::optional<fs::path> &openxr_runtime) {
  auto explicit_value = strip(get(base_, "VR_OVERRIDE"));
  auto registry_override = strip(get(base_, "VR_PATHREG_OVERRIDE"));
  auto resolve = [](const std::string &value) {
    auto path = value.starts_with("~/") ? home_dir() / paths::from_utf8(value.substr(2)) : paths::from_utf8(value);
    return fs::weakly_canonical(fs::absolute(path));
  };
  if (!explicit_value.empty()) {
    // An explicit OpenVR target is authoritative and self-contained.
    auto runtime = resolve(explicit_value);
    if (!exists_file(runtime / "bin/linux64/vrclient.so"))
      throw std::runtime_error("VR_OVERRIDE is not a usable OpenVR runtime: " + paths::utf8(runtime));
    auto selected = registry_override.empty() ? write_openvr_path_registry(runtime) : resolve(registry_override);
    auto registry = private_openvr_path_registry(selected);
    std::string kind = steamvr_runtime_for_openxr(runtime / "steamxr_linux64.json") == runtime ? "steamvr"
                       : exists_file(runtime / "libxrizer.so")                                  ? "xrizer"
                                                                                               : "external";
    return {runtime, registry, kind};
  }
  // When SteamVR is the active OpenXR runtime, use its OpenVR client directly;
  // other OpenXR runtimes go through the bundled XRizer.
  auto runtime_json = openxr_runtime ? *openxr_runtime : active_runtime_json(base_);
  if (auto steamvr = steamvr_runtime_for_openxr(runtime_json)) {
    auto selected = registry_override.empty() ? write_openvr_path_registry(*steamvr) : resolve(registry_override);
    return {*steamvr, private_openvr_path_registry(selected), "steamvr"};
  }
  auto xrizer = install_openvr_runtime();
  return {xrizer, write_openvr_path_registry(xrizer), "xrizer"};
}

fs::path Installer::install_platform_compat() {
  auto source = install_meta_runtime() / "oculus-runtime";
  auto destination = layout_.tools / "platform-compat";
  fs::create_directories(destination);
  for (auto name : {"LibOVRPlatform64_1.dll", "LibOVRP2P64_1.dll"})
    if (exists_file(source / name))
      copy_preserving(source / name, destination / name);
  auto runtime_impl = source / "LibOVRPlatformImpl64_1.dll";
  auto runtime_real = source / "LibOVRPlatformImpl64_1_real.dll";
  // The public Platform DLL resolves its implementation from Meta's runtime
  // folder even when loaded from LIBOVR_DLL_DIR: keep the vendor DLL there
  // once as _real, then put the forwarding shim at the canonical name.
  if (exists_file(runtime_impl) && !exists_file(runtime_real))
    copy_preserving(runtime_impl, runtime_real);
  if (exists_file(runtime_real))
    copy_preserving(runtime_real, destination / "LibOVRPlatformImpl64_1_real.dll");
  auto shim = archive_override("RIFTLIFT_PLATFORM_SHIM");
  if (shim.empty())
    shim = layout_.tools / "rift-runtime/LibOVRPlatformImpl64_1.dll";
  if (!exists_file(shim))
    throw std::runtime_error("RiftLift platform compatibility DLL is missing from the release payload");
  copy_preserving(shim, destination / "LibOVRPlatformImpl64_1.dll");
  copy_preserving(shim, runtime_impl);
  return destination;
}

void Installer::install_openxr_layer() {
  // Registered only in RiftLift's private prefix.
  auto directory = layout_.tools / "rift-runtime";
  auto library = directory / "RiftLiftOpenXRLayer.dll";
  if (!exists_file(library))
    throw std::runtime_error("RiftLift OpenXR compatibility layer is missing");
  auto manifest = directory / "openxr-layer.json";
  nlohmann::ordered_json layer{{"name", "XR_APILAYER_RIFTLIFT_ovr_compat"},
                               {"library_path", linux_to_windows(library)},
                               {"api_version", "1.0"},
                               {"implementation_version", "1"},
                               {"description", "RiftLift OVRPlugin compatibility"},
                               {"enable_environment", "RIFTLIFT_OVR_COMPAT"},
                               {"disable_environment", "RIFTLIFT_DISABLE_OVR_COMPAT"}};
  nlohmann::ordered_json document{{"file_format_version", "1.0.0"}, {"api_layer", layer}};
  auto payload = document.dump(2) + "\n";
  std::string current;
  try {
    current = read_file(manifest);
  } catch (...) {
  }
  if (current != payload)
    write_file(manifest, payload);
  const std::string key = "Software\\Khronos\\OpenXR\\1\\ApiLayers\\Implicit";
  auto name = linux_to_windows(manifest);
  std::string contents;
  try {
    contents = read_file(layout_.prefix / "pfx/system.reg");
  } catch (...) {
  }
  if (!layer_registered(contents, key, name))
    registry_add("HKLM\\" + key, name, "REG_DWORD", "0");
}

void Installer::setup() {
  for (auto &dir : {layout_.data, layout_.cache, layout_.config, layout_.prefix, layout_.tools})
    fs::create_directories(dir);
  install_proton();
  install_meta_runtime();
  install_rift_runtime();
  install_openxr_layer();
  install_openvr_runtime();
  install_platform_compat();
  shutdown_compat_prefix();
}

void Installer::shutdown_compat_prefix(bool wait) {
  auto wineserver = install_proton() / "files/bin/wineserver";
  if (!exists_file(wineserver))
    throw std::runtime_error("GE-Proton is missing wineserver after installation");
  auto env = base_;
  env["WINEPREFIX"] = paths::utf8(layout_.prefix / "pfx");
  env["WINEDEBUG"] = "-all";
  env.erase("LD_PRELOAD");
  SpawnOptions options;
  options.env = &env;
  options.quiet = true;
  std::vector<std::string> argv{paths::utf8(wineserver), "-k"};
  if (wait)
    argv.push_back("-w");
  int code;
  try {
    code = run(wineserver, argv, options, {}, 20s);
  } catch (const std::exception &e) {
    throw std::runtime_error(std::string("could not stop the compatibility prefix: ") + e.what());
  }
  // 1: no server was running for this prefix, which is the desired state.
  if (code != 0 && code != 1)
    throw std::runtime_error(std::format("could not stop the compatibility prefix (wineserver exit {})", code));
}

Env Installer::launch_environment(const fs::path &game_dir, bool platform_shim, bool platform_offline,
                                  const fs::path &runtime_json) {
  install_openxr_layer();
  auto env = proton_environment(game_dir);
  apply_openxr_environment(env, runtime_json);
  if (auto flatpak = flatpak_app_parts(runtime_json)) {
    // A Flatpak runtime (WiVRn) lives in a sandbox Steam's container cannot
    // read unless its app folder is mounted in.
    if (auto dynamic = wivrn_pressure_vessel_env(flatpak->second))
      for (auto &[k, v] : *dynamic)
        env[k] = v;
    else
      env["PRESSURE_VESSEL_FILESYSTEMS_RW"] = paths::utf8(flatpak->first);
  }
  if (platform_shim) {
    install_platform_compat();
    // One canonical public Platform SDK DLL: the vendor loader rejects a
    // loaded DLL path that differs from the installed runtime's.
    env["WINEPATH"] = std::string(meta_runtime_windows);
    if (platform_offline)
      env["RIFTLIFT_PLATFORM_OFFLINE"] = "1";
  }
  return env;
}

void Installer::clear_proton_openvr_cache() {
  // Proton caches its OpenVR runtime in the shared registry; a wineserver
  // first started without XRizer could otherwise keep pointing at SteamVR.
  auto generated = layout_.prefix / "pfx/drive_c/users/steamuser/AppData/Local/openvr/openvrpaths.vrpath";
  std::error_code ec;
  fs::remove(generated, ec);
  if (ec)
    throw std::runtime_error("could not remove Proton's generated OpenVR path registry: " + ec.message());
  auto wine = install_proton() / "files/bin/wine";
  if (!exists_file(wine))
    return;
  auto env = base_;
  env["WINEPREFIX"] = paths::utf8(layout_.prefix / "pfx");
  env["WINEDEBUG"] = "-all";
  // Prefix maintenance may run 32-bit Wine without a matching host injector.
  env.erase("LD_PRELOAD");
  SpawnOptions options;
  options.env = &env;
  options.quiet = true;
  try {
    run(wine, {paths::utf8(wine), "reg.exe", "delete", "HKCU\\Software\\Wine\\VR", "/f"}, options, stop_, 10s);
  } catch (const std::exception &e) {
    throw std::runtime_error(std::string("could not refresh Proton's OpenVR runtime cache: ") + e.what());
  }
}
} // namespace riftlift::runtime
