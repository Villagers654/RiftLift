#include "common/rift/game.h"
#include "common/log.h"
#include <algorithm>
#include <regex>
#include <stdexcept>
namespace riftlift::rift {
namespace {
const std::vector<std::string> &known_fields() {
  static const std::vector<std::string> names{
      "slug",        "name",          "app_id",         "app_key",      "directory",
      "executable",  "arguments",     "version",        "binary_id",    "version_code",
      "platform_shim", "platform_offline", "store_url", "description", "description_lang",
      "developer",   "publisher",     "genres",         "artwork",      "steam_app_id",
      "source",      "launch_options", "dll_overrides", "environment", "pinned"};
  return names;
}
std::vector<std::string> strings(const Json &j, std::string_view key) {
  std::vector<std::string> out;
  const auto &list = field(j, key);
  if (list.is_null())
    return out;
  if (!list.is_array())
    throw std::invalid_argument("game " + std::string(key) + " must be a list of strings");
  for (const auto &value : list) {
    if (!value.is_string())
      throw std::invalid_argument("game " + std::string(key) + " must be a list of strings");
    out.push_back(value.get<std::string>());
  }
  return out;
}
std::map<std::string, std::string> mapping(const Json &j, std::string_view key) {
  std::map<std::string, std::string> out;
  const auto &object = field(j, key);
  if (object.is_null())
    return out;
  if (!object.is_object())
    throw std::invalid_argument("game " + std::string(key) + " must map names to text");
  for (const auto &[name, value] : object.items()) {
    if (!value.is_string())
      throw std::invalid_argument("game " + std::string(key) + " must map names to text");
    out[name] = value.get<std::string>();
  }
  return out;
}
std::string text(const Json &j, std::string_view key) {
  const auto &value = field(j, key);
  if (value.is_null())
    return {};
  if (!value.is_string())
    throw std::invalid_argument("game " + std::string(key) + " must be a string");
  return value.get<std::string>();
}
bool flag(const Json &j, std::string_view key, bool fallback) {
  const auto &value = field(j, key);
  return value.is_boolean() ? value.get<bool>() : fallback;
}
} // namespace

bool valid_slug(std::string_view slug) {
  static const std::regex pattern("[a-z0-9]+(?:-[a-z0-9]+)*");
  return !slug.empty() && slug.size() <= 120 &&
         std::regex_match(slug.begin(), slug.end(), pattern);
}
std::string slugify(std::string_view name) {
  std::string out;
  bool dash = false;
  for (unsigned char c : name) {
    if (std::isalnum(c) && c < 128) {
      if (dash && !out.empty())
        out += '-';
      dash = false;
      out += char(std::tolower(c));
    } else
      dash = true;
  }
  if (out.size() > 120)
    out.resize(120);
  while (!out.empty() && out.back() == '-')
    out.pop_back();
  return out.empty() ? "meta-rift-game" : out;
}
fs::path Game::executable_path() const {
  return directory / paths::from_utf8(executable);
}
void Game::validate() const {
  if (!valid_slug(slug))
    throw std::invalid_argument("invalid game slug: " + slug);
  if (!directory.is_absolute())
    throw std::invalid_argument("game directory must be an absolute path");
  auto relative = paths::from_utf8(executable);
  if (executable.empty() || relative.is_absolute() || relative.filename().empty() ||
      std::ranges::any_of(relative, [](const fs::path &part) { return part == ".."; }))
    throw std::invalid_argument("game executable must stay inside its game directory");
  if (source != "meta" && source != "local" && source != "steam")
    throw std::invalid_argument("invalid game source: " + source);
  for (const auto &option : launch_options)
    if (option.find('\0') != std::string::npos)
      throw std::invalid_argument("game launch options must not contain NUL");
  static const std::regex env_name("[A-Za-z_][A-Za-z0-9_]*");
  for (const auto &[key, value] : environment)
    if (!std::regex_match(key, env_name) || value.find_first_of(std::string("\0\r\n", 3)) != std::string::npos)
      throw std::invalid_argument("Environment variables must use NAME=value with single-line values");
  validate_dll_overrides(dll_overrides);
  if (auto wine = environment.find("WINEDLLOVERRIDES"); wine != environment.end())
    validate_dll_overrides(wine->second);
}
Json Game::json() const {
  Json j = extra.is_object() ? extra : Json::object();
  j["slug"] = slug;
  j["name"] = name;
  j["app_id"] = app_id;
  j["app_key"] = app_key;
  j["directory"] = paths::utf8(directory);
  j["executable"] = executable;
  j["arguments"] = arguments;
  j["version"] = version;
  if (!binary_id.empty())
    j["binary_id"] = binary_id;
  if (!version_code.empty())
    j["version_code"] = version_code;
  j["platform_shim"] = platform_shim;
  j["platform_offline"] = platform_offline;
  if (pinned)
    j["pinned"] = true;
  j["store_url"] = store_url;
  j["description"] = description;
  j["description_lang"] = description_lang;
  j["developer"] = developer;
  j["publisher"] = publisher;
  j["genres"] = genres;
  j["artwork"] = artwork;
  j["steam_app_id"] = steam_app_id;
  j["source"] = source;
  j["launch_options"] = launch_options;
  j["dll_overrides"] = dll_overrides;
  j["environment"] = environment;
  return j;
}
Game Game::parse(const Json &j) {
  if (!j.is_object())
    throw std::invalid_argument("game record is not a JSON object");
  Game g;
  g.slug = text(j, "slug");
  g.name = text(j, "name");
  g.app_id = text(j, "app_id");
  g.app_key = text(j, "app_key");
  g.directory = paths::from_utf8(text(j, "directory"));
  g.executable = text(j, "executable");
  std::ranges::replace(g.executable, '\\', '/');
  g.arguments = strings(j, "arguments");
  g.version = text(j, "version");
  g.binary_id = str(j, "binary_id");
  g.version_code = str(j, "version_code");
  g.platform_shim = flag(j, "platform_shim", true);
  g.platform_offline = flag(j, "platform_offline", false);
  g.pinned = flag(j, "pinned", false);
  g.store_url = text(j, "store_url");
  g.description = text(j, "description");
  g.description_lang = text(j, "description_lang");
  g.developer = text(j, "developer");
  g.publisher = text(j, "publisher");
  g.genres = strings(j, "genres");
  g.artwork = mapping(j, "artwork");
  if (const auto &steam = field(j, "steam_app_id"); !steam.is_null()) {
    if (!steam.is_number_unsigned() && !(steam.is_number_integer() && steam.get<std::int64_t>() >= 0))
      throw std::invalid_argument("game Steam app ID must be a nonnegative integer");
    g.steam_app_id = steam.get<std::uint64_t>();
  }
  // Records from before the source field: Steam games were keyed steam.app.<id>.
  g.source = j.contains("source") ? text(j, "source")
             : g.app_key.starts_with("steam.app.") ? "steam" : "meta";
  g.launch_options = strings(j, "launch_options");
  g.dll_overrides = text(j, "dll_overrides");
  g.environment = mapping(j, "environment");
  for (const auto &[key, value] : j.items())
    if (std::ranges::find(known_fields(), key) == known_fields().end())
      g.extra[key] = value;
  g.validate();
  return g;
}
fs::path state_dir(const fs::path &data) {
#ifdef _WIN32
  return data / "data";
#else
  return data;
#endif
}
fs::path record_path(const fs::path &data, std::string_view slug) {
  if (!valid_slug(slug))
    throw std::invalid_argument("invalid game slug: " + std::string(slug));
  return state_dir(data) / "games" / paths::from_utf8(std::string(slug) + ".json");
}
Game load(const fs::path &data, std::string_view slug) {
  auto file = record_path(data, slug);
  if (!fs::exists(file))
    throw std::runtime_error("Unknown game " + std::string(slug) + "; add it to RiftLift first.");
  auto game = Game::parse(paths::read_json(file));
  if (game.slug != slug)
    throw std::runtime_error("The record " + paths::utf8(file) + " names another game.");
  return game;
}
void save(const fs::path &data, const Game &game) {
  game.validate();
  auto file = record_path(data, game.slug);
  fs::create_directories(file.parent_path());
  paths::atomic_write(file, game.json().dump(2) + "\n");
}
void remove_record(const fs::path &data, std::string_view slug) {
  std::error_code ec;
  fs::remove(record_path(data, slug), ec);
}
Records load_all(const fs::path &data) {
  Records out;
  std::error_code ec;
  auto folder = state_dir(data) / "games";
  if (!fs::is_directory(folder, ec))
    return out;
  std::vector<fs::path> files;
  for (const auto &entry : fs::directory_iterator(folder, ec))
    if (entry.path().extension() == ".json")
      files.push_back(entry.path());
  if (ec)
    out.complete = false;
  std::ranges::sort(files);
  for (const auto &file : files) {
    try {
      auto game = Game::parse(paths::read_json(file));
      if (paths::utf8(file.stem()) != game.slug)
        throw std::invalid_argument("its slug does not match the file name");
      out.games.push_back(std::move(game));
    } catch (const std::exception &e) {
      out.complete = false;
      log::warn("library", "Skipped the game record {}: {}", paths::utf8(file), e.what());
    }
  }
  return out;
}
std::vector<std::string> split_arguments(std::string_view value) {
  // POSIX shell words with quotes but no escape character, like the launch
  // strings in Meta's manifests ("-vr -oculus", "\"C:\\x y\" -flag").
  std::vector<std::string> out;
  std::string word;
  bool in_word = false;
  char quote = 0;
  for (char c : value) {
    if (quote) {
      if (c == quote)
        quote = 0;
      else
        word += c;
      continue;
    }
    if (c == '"' || c == '\'') {
      quote = c;
      in_word = true;
    } else if (std::isspace(static_cast<unsigned char>(c))) {
      if (in_word)
        out.push_back(std::exchange(word, {}));
      in_word = false;
    } else {
      word += c;
      in_word = true;
    }
  }
  if (quote)
    throw std::invalid_argument("No closing quotation");
  if (in_word)
    out.push_back(word);
  return out;
}
std::string join_arguments(const std::vector<std::string> &arguments) {
  std::string out;
  for (const auto &argument : arguments) {
    if (!out.empty())
      out += ' ';
    bool plain = !argument.empty() && std::ranges::none_of(argument, [](char c) {
      return std::isspace(static_cast<unsigned char>(c)) || c == '"' || c == '\'';
    });
    if (plain) {
      out += argument;
      continue;
    }
    // Quotes cannot be escaped: close the double-quoted run around each
    // literal double quote and supply it single-quoted instead.
    size_t start = 0;
    for (;;) {
      auto end = argument.find('"', start);
      out += '"' + argument.substr(start, end - start) + '"';
      if (end == std::string::npos)
        break;
      out += "'\"'";
      start = end + 1;
    }
  }
  return out;
}
void validate_dll_overrides(std::string_view value) {
  static const std::regex name("\\*?[a-zA-Z0-9_.-]+|\\*");
  size_t start = 0;
  while (start <= value.size()) {
    auto end = value.find(';', start);
    auto rule = value.substr(start, end == value.npos ? value.npos : end - start);
    start = end == value.npos ? value.size() + 1 : end + 1;
    if (rule.find_first_not_of(" \t") == rule.npos)
      continue;
    auto equals = rule.find('=');
    bool ok = equals != rule.npos;
    if (ok) {
      auto names = rule.substr(0, equals);
      size_t at = 0;
      while (ok && at <= names.size()) {
        auto comma = names.find(',', at);
        auto part = std::string(names.substr(at, comma == names.npos ? names.npos : comma - at));
        part.erase(0, part.find_first_not_of(" \t"));
        part.erase(part.find_last_not_of(" \t") + 1);
        ok = std::regex_match(part, name);
        at = comma == names.npos ? names.size() + 1 : comma + 1;
      }
      std::string order;
      for (char c : rule.substr(equals + 1))
        if (c != ' ')
          order += char(std::tolower(static_cast<unsigned char>(c)));
      ok = ok && (order.empty() || order == "n" || order == "b" || order == "n,b" || order == "b,n");
    }
    if (!ok)
      throw std::invalid_argument("Use DLL overrides such as version=n,b;winhttp=n,b");
  }
}
} // namespace riftlift::rift
