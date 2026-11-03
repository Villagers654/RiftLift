#pragma once
// A game RiftLift can launch: one record per game in <data>/games/<slug>.json.
// Store installs, local games (an .exe installed elsewhere) and Steam games
// with an Oculus mode all have one; the runtime reads it at launch. The
// format is the one RiftLift has always written, so existing records load.
#include "common/json.h"
#include "common/paths.h"
#include <map>
#include <string>
#include <vector>
namespace riftlift::rift {
struct Game {
  std::string slug, name, app_id, app_key;
  fs::path directory;
  std::string executable;  // relative to directory, '/' separated
  std::vector<std::string> arguments;
  std::string version;
  // The Meta build installed (store installs only; absent in older records).
  std::string binary_id, version_code;
  // A version chosen by hand, installed beside the game's other versions: it
  // keeps that build and is its own library entry.
  bool pinned = false;
  bool platform_shim = true, platform_offline = false;
  std::string store_url, description, description_lang, developer, publisher;
  std::vector<std::string> genres;
  std::map<std::string, std::string> artwork;
  std::uint64_t steam_app_id = 0;
  std::string source = "meta";  // meta, local or steam
  std::vector<std::string> launch_options;
  std::string dll_overrides;
  std::map<std::string, std::string> environment;
  // Fields written by another RiftLift build, kept when this one saves.
  Json extra = Json::object();

  fs::path executable_path() const;
  // Throws std::invalid_argument naming the first invalid field.
  void validate() const;
  Json json() const;
  static Game parse(const Json &record);
};
bool valid_slug(std::string_view slug);
// "Lone Echo: Deluxe" -> "lone-echo-deluxe".
std::string slugify(std::string_view name);
// Where RiftLift keeps game records and playtime: the data folder on Linux,
// its "data" subfolder on Windows (the layouts RiftLift always used).
fs::path state_dir(const fs::path &data);
fs::path record_path(const fs::path &data, std::string_view slug);
Game load(const fs::path &data, std::string_view slug);
void save(const fs::path &data, const Game &game);
void remove_record(const fs::path &data, std::string_view slug);
struct Records {
  std::vector<Game> games;
  bool complete = true;  // false when a record could not be read
};
Records load_all(const fs::path &data);
// A Windows launch string split like the game's own command line would be,
// without keeping quotes or eating backslashes.
std::vector<std::string> split_arguments(std::string_view value);
std::string join_arguments(const std::vector<std::string> &arguments);
// Wine DLL override rules such as "version=n,b;winhttp=n,b".
void validate_dll_overrides(std::string_view value);
} // namespace riftlift::rift
