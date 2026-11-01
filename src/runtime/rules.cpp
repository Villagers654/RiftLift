#include "runtime/rules.h"
#include "common/rift/game.h"
#include <algorithm>
#include <charconv>
#include <regex>
#include <stdexcept>

namespace riftlift::runtime {
namespace {
constexpr std::string_view whitespace = " \t\r\n\f\v";
std::vector<std::string_view> split(std::string_view text, char separator) {
  std::vector<std::string_view> parts;
  while (true) {
    auto end = text.find(separator);
    parts.push_back(text.substr(0, end));
    if (end == text.npos)
      return parts;
    text = text.substr(end + 1);
  }
}
std::string join(const std::vector<std::string> &parts, std::string_view separator) {
  std::string out;
  for (size_t i = 0; i < parts.size(); ++i)
    out += (i ? std::string(separator) : std::string()) + parts[i];
  return out;
}
// mods.py _dll_name: "*Foo.DLL" -> "foo"; a lone "*" stays.
std::string dll_name(std::string_view value) {
  auto name = lower(strip(value));
  if (name.ends_with(".dll"))
    name.resize(name.size() - 4);
  if (name == "*")
    return name;
  name.erase(0, name.find_first_not_of('*'));
  return name;
}
} // namespace

std::string lower(std::string_view text) {
  std::string out(text);
  std::ranges::transform(out, out.begin(), [](unsigned char c) { return char(std::tolower(c)); });
  return out;
}
std::string strip(std::string_view text) {
  auto first = text.find_first_not_of(whitespace);
  if (first == text.npos)
    return {};
  return std::string(text.substr(first, text.find_last_not_of(whitespace) - first + 1));
}
std::string wine_path(const fs::path &absolute) {
  auto text = absolute.generic_string();
  if (!text.starts_with('/'))
    return text;
  std::ranges::replace(text, '/', '\\');
  return "Z:" + text;
}
std::string linux_to_windows(const fs::path &path) {
  std::error_code ec;
  auto resolved = fs::weakly_canonical(fs::absolute(path, ec), ec);
  return wine_path(ec ? fs::absolute(path) : resolved);
}

void apply_dll_overrides(Env &env, std::string_view overrides) {
  rift::validate_dll_overrides(overrides);
  std::vector<std::string> rules;
  for (auto rule : split(overrides, ';'))
    if (auto text = strip(rule); !text.empty())
      rules.push_back(text);
  if (rules.empty())
    return;
  std::set<std::string> chosen;
  for (auto &rule : rules)
    for (auto name : split(std::string_view(rule).substr(0, rule.find('=')), ','))
      chosen.insert(dll_name(name));
  std::vector<std::string> retained;
  const auto existing = get(env, "WINEDLLOVERRIDES");
  for (auto rule : split(existing, ';')) {
    auto equals = rule.find('=');
    if (equals == rule.npos || chosen.contains("*"))
      continue;
    std::vector<std::string> remaining;
    for (auto name : split(rule.substr(0, equals), ','))
      if (!chosen.contains(dll_name(name)))
        remaining.emplace_back(name);
    if (!remaining.empty())
      retained.push_back(join(remaining, ",") + "=" + std::string(rule.substr(equals + 1)));
  }
  retained.insert(retained.end(), rules.begin(), rules.end());
  env["WINEDLLOVERRIDES"] = join(retained, ";");
}

void configure_mod_loaders(Env &env, const fs::path &executable) {
  std::map<std::string, fs::directory_entry> entries;
  std::error_code ec;
  for (const auto &entry : fs::directory_iterator(executable.parent_path(), ec))
    entries.emplace(lower(paths::utf8(entry.path().filename())), entry);
  if (ec)
    return;
  std::set<std::string> proxies;
  const std::pair<std::string_view, std::vector<std::string>> loaders[] = {
      {"melonloader", {"version", "winhttp", "winmm"}}, {"bepinex", {"winhttp"}}};
  for (auto &[folder, names] : loaders) {
    auto loader = entries.find(std::string(folder));
    if (loader == entries.end() || !loader->second.is_directory(ec))
      continue;
    for (auto &name : names)
      if (auto dll = entries.find(name + ".dll"); dll != entries.end() && dll->second.is_regular_file(ec))
        proxies.insert(name);
  }
  if (proxies.empty())
    return;
  const auto existing = get(env, "WINEDLLOVERRIDES");
  std::set<std::string> explicit_names;
  for (auto rule : split(existing, ';'))
    if (auto equals = rule.find('='); equals != rule.npos)
      for (auto name : split(rule.substr(0, equals), ','))
        explicit_names.insert(dll_name(name));
  if (explicit_names.contains("*"))
    return;
  std::vector<std::string> parts;
  if (!existing.empty()) {
    auto trimmed = existing;
    while (trimmed.ends_with(';'))
      trimmed.pop_back();
    parts.push_back(trimmed);
  }
  bool added = false;
  for (auto &name : proxies)  // std::set: sorted, as Python's sorted()
    if (!explicit_names.contains(name)) {
      parts.push_back(name + "=n,b");
      added = true;
    }
  if (added)
    env["WINEDLLOVERRIDES"] = join(parts, ";");
}

std::string select_backend(std::string_view override_value, const std::vector<std::string> &arguments,
                           const std::set<std::string> &capabilities) {
  if (auto value = lower(strip(override_value)); !value.empty()) {
    if (value != "openxr" && value != "openvr")
      throw std::runtime_error("RIFTLIFT_RUNTIME_BACKEND must be 'openxr' or 'openvr'");
    return value;
  }
  // The direct bridge does not implement D3D12 or every legacy OVR presentation path.
  const bool legacy = std::ranges::any_of(arguments, [](const std::string &argument) {
    auto value = lower(argument);
    return value == "-ovr" || value == "-vr_presentation";
  });
  for (auto name : {"openvr", "unity-oculus-plugin", "d3d12"})
    if (capabilities.contains(name))
      return "openvr";
  return legacy ? "openvr" : "openxr";
}

std::vector<std::string> oculus_launch_arguments(Engine engine, std::vector<std::string> arguments) {
  if (engine == Engine::unreal) {
    // Unreal needs both stereo rendering and an explicit Oculus plugin selection.
    std::erase_if(arguments, [](const std::string &argument) {
      auto value = lower(argument);
      return value == "-openvr" || value == "-steamvr" || value == "-openxr";
    });
    auto has = [&](std::string_view flag) {
      return std::ranges::any_of(arguments, [&](auto &a) { return lower(a) == flag; });
    };
    const bool vr = has("-vr"), oculus = has("-oculus");
    if (!vr)
      arguments.push_back("-vr");
    if (!oculus)
      arguments.push_back("-oculus");
  } else if (engine == Engine::unity) {
    // Replace any inherited Unity runtime selector instead of stacking one.
    std::vector<std::string> normalized;
    for (size_t i = 0; i < arguments.size(); ++i) {
      if (lower(arguments[i]) == "-vrmode") {
        ++i;
        continue;
      }
      normalized.push_back(arguments[i]);
    }
    normalized.insert(normalized.end(), {"-vrmode", "Oculus"});
    arguments = std::move(normalized);
  }
  return arguments;
}

std::vector<std::string> launcher_arguments(const std::string &launcher, std::string_view backend,
                                            const std::string &app_key, const std::string &cwd,
                                            const std::string &executable,
                                            const std::vector<std::string> &arguments) {
  std::vector<std::string> out{launcher, "/wait", "/" + std::string(backend), "/app", app_key,
                               "/cwd",   cwd,     executable};
  out.insert(out.end(), arguments.begin(), arguments.end());
  return out;
}

void disable_openxr_for_direct_openvr(Env &env, std::string_view openvr_kind) {
  if (openvr_kind == "xrizer")
    return;
  for (auto name : {"XR_RUNTIME_JSON", "PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES",
                    "PRESSURE_VESSEL_FILESYSTEMS_RW", "OXR_ZERO_TIME_IS_NOW"})
    env.erase(name);
  auto overrides = get(env, "WINEDLLOVERRIDES");
  overrides.erase(0, overrides.find_first_not_of(';'));
  while (overrides.ends_with(';'))
    overrides.pop_back();
  env["WINEDLLOVERRIDES"] = "wineopenxr=d" + (overrides.empty() ? std::string() : ";" + overrides);
}

void configure_proton_identity(Env &env, std::string_view source, std::uint64_t steam_app_id) {
  if (source == "steam" && steam_app_id) {
    auto id = std::to_string(steam_app_id);
    env["SteamAppId"] = id;
    env["SteamGameId"] = id;
    env["UMU_ID"] = "umu-" + id;
  } else {
    env["UMU_ID"] = "umu-default";
  }
  env["UMU_USE_STEAM"] = "0";
}

std::optional<std::pair<fs::path, std::string>> flatpak_app_parts(const fs::path &runtime) {
  std::vector<fs::path> parts(runtime.begin(), runtime.end());
  for (size_t i = 0; i < parts.size(); ++i) {
    if (parts[i] != "flatpak" || i + 1 >= parts.size() || parts[i + 1] != "app")
      continue;
    if (i + 2 >= parts.size())
      return std::nullopt;
    fs::path root;
    for (size_t j = 0; j <= i + 2; ++j)
      root /= parts[j];
    return std::pair{root, paths::utf8(parts[i + 2])};
  }
  return std::nullopt;
}

std::optional<std::map<std::string, std::string>> parse_pressure_vessel_line(
    std::string_view line, const std::function<bool(const fs::path &)> &is_directory) {
  constexpr std::string_view marker = "set command to ";
  auto at = line.find(marker);
  if (at == line.npos)
    return std::nullopt;
  std::string remainder(line.substr(at + marker.size()));
  for (size_t p; (p = remainder.find("%command%")) != remainder.npos;)
    remainder.erase(p, 9);
  static const std::regex key("PRESSURE_VESSEL_[A-Z0-9_]+");
  static const std::regex path_token("/[A-Za-z0-9_./-]+");
  std::map<std::string, std::string> pairs;
  size_t start = 0;
  while (true) {
    auto first = remainder.find_first_not_of(whitespace, start);
    if (first == remainder.npos)
      break;
    auto last = remainder.find_first_of(whitespace, first);
    std::string token = remainder.substr(first, last == remainder.npos ? remainder.npos : last - first);
    start = last == remainder.npos ? remainder.size() : last;
    auto equals = token.find('=');
    if (equals == token.npos)
      return std::nullopt;
    auto name = token.substr(0, equals), value = token.substr(equals + 1);
    if (!std::regex_match(name, key))
      return std::nullopt;
    if (value != "0" && value != "1")
      for (auto part : split(value, ':'))
        if (!std::regex_match(part.begin(), part.end(), path_token) ||
            !is_directory(paths::from_utf8(part)))
          return std::nullopt;
    pairs[name] = value;
  }
  if (pairs.empty())
    return std::nullopt;
  return pairs;
}

bool layer_registered(std::string_view system_reg, std::string_view key, std::string_view name) {
  auto escape = [](std::string_view text) {
    std::string out;
    for (char c : text)
      out += c == '\\' ? std::string("\\\\") : std::string(1, c);
    return out;
  };
  const auto section = "[" + escape(key) + "]";
  const auto value = "\"" + escape(name) + "\"=dword:00000000";
  size_t start = 0;
  while (start <= system_reg.size()) {
    auto end = system_reg.find("\n\n", start);
    auto block = system_reg.substr(start, end == system_reg.npos ? system_reg.npos : end - start);
    start = end == system_reg.npos ? system_reg.size() + 1 : end + 2;
    if (!block.starts_with(section))
      continue;
    for (auto line : split(block, '\n')) {
      if (line.ends_with('\r'))  // Python's splitlines() drops \r too
        line.remove_suffix(1);
      if (line == value)
        return true;
    }
  }
  return false;
}

Env proton_environment(Env env, const ProtonSettings &settings) {
  for (auto name : {"VR_OVERRIDE", "VR_PATHREG_OVERRIDE", "XR_RUNTIME_JSON",
                    "PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES", "OXR_ZERO_TIME_IS_NOW",
                    "RIFTLIFT_XRIZER"})
    env.erase(name);
  auto wine_debug = get(env, "RIFTLIFT_WINEDEBUG");
  if (wine_debug.empty())
    wine_debug = settings.debug ? std::string(debug_wine_channels) : "-all";
  env["STEAM_COMPAT_DATA_PATH"] = paths::utf8(settings.prefix);
  env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = paths::utf8(settings.steam_root);
  env["STEAM_COMPAT_INSTALL_PATH"] = paths::utf8(settings.install_path);
  env["STEAM_COMPAT_LIBRARY_PATHS"] = paths::utf8(settings.steam_root / "steamapps");
  env["SteamAppId"] = "0";
  env["SteamGameId"] = "0";
  env["PROTON_LOG"] = settings.debug ? "1" : "0";
  env["WINEDEBUG"] = wine_debug;
  if (settings.debug) {
    auto keep = [&](const char *name, const char *fallback) {
      if (!env.contains(name))
        env[name] = fallback;
    };
    env["PROTON_LOG_DIR"] = paths::utf8(settings.proton_logs);
    env["PROTON_CRASH_REPORT_DIR"] = paths::utf8(settings.crash_logs);
    keep("DXVK_LOG_LEVEL", "debug");
    env["DXVK_LOG_PATH"] = paths::utf8(settings.graphics_logs);
    keep("VKD3D_DEBUG", "info");
    keep("VKD3D_SHADER_DEBUG", "warn");
    keep("VK_LOADER_DEBUG", "error,warn,info");
    keep("XR_LOADER_DEBUG", "all");
    auto rust = get(env, "RIFTLIFT_RUST_LOG");
    env["RUST_LOG"] = env.contains("RIFTLIFT_RUST_LOG") ? rust : "info,xrizer_tracking=debug";
    env["XRIZER_LOG_DIR"] = paths::utf8(settings.openvr_logs);
  }
  return env;
}
void apply_openxr_environment(Env &env, const fs::path &runtime_json) {
  auto existing = strip(get(env, "WINEDLLOVERRIDES"));
  existing.erase(0, existing.find_first_not_of(';'));
  while (existing.ends_with(';'))
    existing.pop_back();
  env["XR_RUNTIME_JSON"] = paths::utf8(runtime_json);
  env["PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES"] = "1";
  env["OXR_ZERO_TIME_IS_NOW"] = "1";
  env["RIFTLIFT_OVR_COMPAT"] = "1";
  env["WINEDLLOVERRIDES"] = "d3d11=n;dxgi=n" + (existing.empty() ? std::string() : ";" + existing);
}
void configure_openvr_environment(Env &env, const fs::path &config_dir, const fs::path &rift_runtime,
                                  const fs::path &openvr_runtime, const fs::path &openvr_registry,
                                  std::string_view openvr_kind) {
  auto manifest = rift_runtime / "Input/action_manifest.json";
  // XRizer reads the manifest natively; Windows-side runtimes need a Wine path.
  env["RIFTLIFT_ACTION_MANIFEST"] =
      openvr_kind == "xrizer" ? paths::utf8(manifest) : linux_to_windows(manifest);
  env["VR_OVERRIDE"] = paths::utf8(openvr_runtime);
  env["VR_PATHREG_OVERRIDE"] = paths::utf8(openvr_registry);
  env["XDG_CONFIG_HOME"] = paths::utf8(config_dir);
  if (openvr_kind == "xrizer") {
    env["RIFTLIFT_XRIZER"] = "1";
  } else {
    env.erase("RIFTLIFT_XRIZER");
    env.erase("XRIZER_LOG_DIR");
  }
}

std::vector<std::string> shell_split(std::string_view text) {
  std::vector<std::string> words;
  std::string word;
  bool in_word = false;
  for (size_t i = 0; i < text.size(); ++i) {
    char c = text[i];
    if (whitespace.find(c) != whitespace.npos) {
      if (in_word)
        words.push_back(std::exchange(word, {}));
      in_word = false;
    } else if (c == '\'') {
      auto end = text.find('\'', i + 1);
      if (end == text.npos)
        throw std::runtime_error("No closing quotation");
      word += text.substr(i + 1, end - i - 1);
      in_word = true;
      i = end;
    } else if (c == '"') {
      in_word = true;
      for (++i;; ++i) {
        if (i >= text.size())
          throw std::runtime_error("No closing quotation");
        if (text[i] == '"')
          break;
        if (text[i] == '\\' && i + 1 < text.size() && (text[i + 1] == '"' || text[i + 1] == '\\'))
          ++i;
        word += text[i];
      }
    } else if (c == '\\') {
      if (i + 1 >= text.size())
        throw std::runtime_error("No escaped character");
      word += text[++i];
      in_word = true;
    } else {
      word += c;
      in_word = true;
    }
  }
  if (in_word)
    words.push_back(word);
  return words;
}

std::string validate_user_id(std::string_view value, std::string_view source) {
  auto text = strip(value);
  if (text.starts_with('+'))
    text.erase(0, 1);
  std::uint64_t parsed = 0;
  auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), parsed);
  if (text.empty() || text.find_first_not_of("0123456789") != text.npos)
    throw std::runtime_error(std::string(source) + " must contain a positive integer");
  if (error != std::errc{} || end != text.data() + text.size() || parsed == 0)
    throw std::runtime_error(std::string(source) + " must contain a positive 64-bit integer");
  return std::to_string(parsed);
}
} // namespace riftlift::runtime
