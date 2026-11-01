#include "common/rift/local_games.h"
#include "common/log.h"
#include "common/rift/detection.h"
#include <algorithm>
namespace riftlift::rift {
namespace {
std::string lower(std::string value) {
  std::ranges::transform(value, value.begin(),
                         [](unsigned char c) { return char(std::tolower(c)); });
  return value;
}
Json section(const fs::path &file, std::string_view name) {
  try {
    auto all = parse_text_vdf(paths::read(file, 8 * 1024 * 1024));
    for (const auto &[key, value] : all.items())
      if (lower(key) == lower(std::string(name)) && value.is_object())
        return value;
  } catch (const std::exception &e) {
    log::warn("steam", "Could not read {}: {}", paths::utf8(file), e.what());
  }
  return Json::object();
}
std::string value_of(const Json &object, std::string_view key) {
  for (const auto &[name, value] : object.items())
    if (lower(name) == lower(std::string(key)) && value.is_string())
      return value.get<std::string>();
  return {};
}
} // namespace
Game local_game(const fs::path &file, std::string name, fs::path root, std::string_view arguments) {
  std::error_code ec;
  auto executable = fs::weakly_canonical(file, ec);
  if (ec || !fs::is_regular_file(executable))
    throw std::runtime_error("The game's executable was not found: " + paths::utf8(file));
  if (lower(paths::utf8(executable.extension())) != ".exe")
    throw std::runtime_error("Local games must point to a Windows .exe file.");
  if (root.empty()) {
    auto parent = executable.parent_path();
    // UE4's Oculus Store layout: <game>/bin/win10/<game>.exe
    root = lower(paths::utf8(parent.filename())) == "win10" &&
                   lower(paths::utf8(parent.parent_path().filename())) == "bin"
               ? parent.parent_path().parent_path()
               : parent;
  } else
    root = fs::weakly_canonical(root, ec);
  if (!fs::is_directory(root))
    throw std::runtime_error("The game folder was not found: " + paths::utf8(root));
  auto relative = executable.lexically_relative(root);
  if (relative.empty() || paths::utf8(*relative.begin()) == "..")
    throw std::runtime_error("The executable must be inside the game folder.");
  auto trimmed = [](std::string s) {
    s.erase(0, s.find_first_not_of(" \t"));
    s.erase(s.find_last_not_of(" \t") + 1);
    return s;
  };
  name = trimmed(name.empty() ? paths::utf8(executable.stem()) : name);
  if (name.empty())
    throw std::runtime_error("The game's name cannot be empty.");
  Game g;
  g.slug = slugify(name);
  g.name = name;
  g.app_key = "local." + g.slug;
  if (root != executable.parent_path() && lower(paths::utf8(executable.parent_path().filename())) == "win10")
    g.app_key = paths::utf8(root.filename());
  g.directory = root;
  g.executable = relative.generic_string();
  g.arguments = split_arguments(arguments);
  g.platform_shim = true;
  g.platform_offline = false;
  g.source = "local";
  g.validate();
  return g;
}
Json parse_text_vdf(std::string_view text) {
  size_t at = 0;
  auto skip = [&] {
    for (;;) {
      while (at < text.size() && std::isspace(static_cast<unsigned char>(text[at])))
        ++at;
      if (text.substr(at, 2) == "//") {
        while (at < text.size() && text[at] != '\n')
          ++at;
        continue;
      }
      return;
    }
  };
  auto token = [&]() -> std::string {
    skip();
    if (at >= text.size())
      throw std::runtime_error("Truncated Steam file.");
    if (text[at] != '"') {
      auto start = at;
      while (at < text.size() && !std::isspace(static_cast<unsigned char>(text[at])) && text[at] != '{' &&
             text[at] != '}' && text[at] != '"')
        ++at;
      return std::string(text.substr(start, at - start));
    }
    std::string out;
    for (++at; at < text.size() && text[at] != '"'; ++at) {
      if (text[at] == '\\' && at + 1 < text.size()) {
        // Steam escapes only these; any other backslash is literal (a path).
        char next = text[at + 1];
        if (next == 'n' || next == 't' || next == '"' || next == '\\') {
          ++at;
          out += next == 'n' ? '\n' : next == 't' ? '\t' : next;
        } else
          out += '\\';
      } else
        out += text[at];
    }
    if (at >= text.size())
      throw std::runtime_error("Unterminated string in a Steam file.");
    ++at;
    return out;
  };
  std::function<Json(int)> object = [&](int depth) -> Json {
    if (depth > 64)
      throw std::runtime_error("Steam file nesting is too deep.");
    Json out = Json::object();
    for (;;) {
      skip();
      if (at >= text.size()) {
        if (depth)
          throw std::runtime_error("Truncated Steam file.");
        return out;
      }
      if (text[at] == '}') {
        if (!depth)
          throw std::runtime_error("Unexpected brace in a Steam file.");
        ++at;
        return out;
      }
      auto key = token();
      skip();
      if (at < text.size() && text[at] == '{') {
        ++at;
        out[key] = object(depth + 1);
      } else
        out[key] = token();
    }
  };
  return object(0);
}
std::vector<fs::path> steam_library_roots(const fs::path &steam_root) {
  std::vector<fs::path> out;
  std::error_code ec;
  auto root = fs::weakly_canonical(steam_root, ec);
  if (ec || !fs::is_directory(root))
    return out;
  out.push_back(root);
  const auto folders = section(root / "steamapps" / "libraryfolders.vdf", "libraryfolders");
  for (const auto &[key, value] : folders.items()) {
    auto path = paths::from_utf8(value_of(value, "path"));
    if (path.empty())
      continue;
    auto candidate = fs::weakly_canonical(path, ec);
    if (!ec && fs::is_directory(candidate / "steamapps") && std::ranges::find(out, candidate) == out.end())
      out.push_back(candidate);
  }
  return out;
}
std::vector<Game> steam_oculus_games(const fs::path &steam_root) {
  std::vector<Game> out;
  for (const auto &library : steam_library_roots(steam_root)) {
    std::vector<fs::path> manifests;
    std::error_code ec;
    for (const auto &entry : fs::directory_iterator(library / "steamapps", ec)) {
      auto name = paths::utf8(entry.path().filename());
      if (name.starts_with("appmanifest_") && name.ends_with(".acf"))
        manifests.push_back(entry.path());
    }
    std::ranges::sort(manifests);
    for (const auto &manifest : manifests) {
      auto state = section(manifest, "AppState");
      auto app_id = value_of(state, "appid"), install = value_of(state, "installdir");
      if (!digits(app_id) || install.empty() || !paths::safe_name(install))
        continue;
      auto directory = library / "steamapps" / "common" / paths::from_utf8(install);
      if (!fs::is_directory(directory, ec) || !uses_oculus_runtime(directory))
        continue;
      fs::path executable;
      try {
        executable = best_windows_executable(directory);
      } catch (...) {
        continue;
      }
      auto name = value_of(state, "name");
      if (name.empty())
        name = install;
      auto root = fs::weakly_canonical(directory, ec);
      Game g;
      g.slug = slugify(name);
      g.name = name;
      g.app_id = app_id;
      g.app_key = "steam.app." + app_id;
      g.directory = root;
      g.executable = executable.lexically_relative(root).generic_string();
      if (is_unreal_shipping(executable))
        g.arguments = {"-vr"};
      g.version = value_of(state, "buildid");
      g.platform_shim = false;
      g.store_url = "https://store.steampowered.com/app/" + app_id + "/";
      g.steam_app_id = number(Json(app_id));
      g.source = "steam";
      out.push_back(std::move(g));
    }
  }
  return out;
}
Game add_game(const fs::path &data, Game game) {
  auto records = load_all(data);
  auto same = std::ranges::find_if(records.games, [&](const Game &g) {
    return g.source == game.source && g.app_key == game.app_key &&
           (game.source == "steam" || g.executable_path() == game.executable_path());
  });
  if (same != records.games.end()) {
    // Re-adding keeps the user's own launch settings.
    game.slug = same->slug;
    game.launch_options = same->launch_options;
    game.dll_overrides = same->dll_overrides;
    game.environment = same->environment;
  } else {
    auto taken = [&](const std::string &slug) {
      return std::ranges::any_of(records.games, [&](const Game &g) { return g.slug == slug; }) ||
             fs::exists(record_path(data, slug));
    };
    if (taken(game.slug)) {
      auto base = game.slug + "-" + game.source;
      auto slug = base;
      for (int suffix = 2; taken(slug); ++suffix)
        slug = base + "-" + std::to_string(suffix);
      game.slug = slug;
    }
  }
  save(data, game);
  return game;
}
} // namespace riftlift::rift
