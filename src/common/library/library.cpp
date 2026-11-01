#include "common/library/library.h"
#include "common/environment.h"
#include "common/file_ops.h"
#include "common/log.h"
#include "common/rift/detection.h"
#include <algorithm>
#include <fstream>
#include <set>
namespace riftlift::library {
Install from_record(const rift::Game &game) {
  Install install;
  install.game = game;
  install.directory = game.directory;
  install.label = game.name;
  auto &p = install.plan;
  p.local = game.source != "meta";
  // Local and Steam games have no Meta listing: their slug is their key.
  p.app_id = !p.local && digits(game.app_id, 5) ? game.app_id : game.slug;
  p.binary_id = game.binary_id;
  p.package = game.slug;
  p.name = game.name;
  p.version = game.version;
  p.version_code = game.version_code.empty() ? "0" : game.version_code;
  std::error_code ec;
  install.complete = fs::is_regular_file(game.executable_path(), ec);
  return install;
}
std::vector<Install> scan(const fs::path &data, bool *authoritative) {
  auto records = rift::load_all(data);
  if (authoritative)
    *authoritative = records.complete;
  std::vector<Install> result;
  for (const auto &game : records.games) {
    auto install = from_record(game);
    install.played = playtime::get(rift::state_dir(data), game.slug);
    // Size on disk: the game's folder (scan runs on the launcher's worker).
    std::error_code ec;
    for (fs::recursive_directory_iterator it(game.directory, fs::directory_options::skip_permission_denied, ec), end;
         !ec && it != end; it.increment(ec))
      if (it->is_regular_file(ec))
        install.bytes += it->file_size(ec);
    result.push_back(std::move(install));
  }
  return result;
}
void merge(std::vector<meta::Game> &games, const meta::Snapshot &s,
           std::string_view account) {
  if (account.empty() || (!s.account_id.empty() && s.account_id != account))
    throw std::runtime_error(
        "Meta account changed. Sign in again before refreshing.");
  std::set<std::string> confirmed;
  for (auto &g : s.games) {
    if (!g.owned || !digits(g.id, 5))
      throw std::runtime_error("Invalid Rift entitlement snapshot.");
    confirmed.insert(g.id);
  }
  // Without a stable identity, absence is never used to revoke cached
  // entries. Games added by store link (cross-buy) are never in the PC
  // library, so it never revokes them either.
  if (s.complete && !s.account_id.empty())
    for (auto &g : games)
      if (g.ownership_account == account && !confirmed.contains(g.id) &&
          g.platform == "PC")
        g.owned = false;
  for (auto g : s.games) {
    g.ownership_account = account;
    auto found = std::ranges::find_if(games, [&](auto &old) {
      return old.id == g.id && old.ownership_account == account;
    });
    // Store text Meta left out of this snapshot: this account's previous entry
    // first, else what another entry for the same app already knows.
    auto known = [&](std::string meta::Game::*member) -> std::string {
      if (found != games.end() && !((*found).*member).empty())
        return (*found).*member;
      for (const auto &old : games)
        if (old.id == g.id && !(old.*member).empty())
          return old.*member;
      return {};
    };
    if (g.description.empty())
      g.description = known(&meta::Game::description);
    if (g.publisher.empty())
      g.publisher = known(&meta::Game::publisher);
    if (g.canonical.empty())
      g.canonical = known(&meta::Game::canonical);
    if (found == games.end())
      games.push_back(g);
    else {
      if (g.image.empty())
        g.image = found->image;
      // Keep the folder name an earlier install used.
      if (!found->package.empty())
        g.package = found->package;
      g.recent = found->recent;
      *found = std::move(g);
    }
  }
}
std::vector<meta::Game> load(const fs::path &file) {
  if (!fs::exists(file))
    return {};
  auto j = paths::read_json(file);
  std::vector<meta::Game> games;
  if (number(j, "version") != 1 || !field(j, "games").is_array())
    throw std::runtime_error(
        "Library cache is unreadable. Refresh your library.");
  for (auto &r : j["games"]) {
    if (!digits(str(r, "id"), 5))
      continue;
    meta::Game g;
    g.id = str(r, "id");
    g.name = str(r, "name");
    g.description = str(r, "description");
    g.publisher = str(r, "publisher");
    g.platform = str(r, "platform");
    g.canonical = str(r, "canonical");
    g.package = str(r, "package");
    if (!rift::valid_slug(g.package))
      g.package = rift::slugify(g.name);
    g.ownership_account = str(r, "accountId");
    g.owned = field(r, "owned") == true;
    g.system = field(r, "system") == true;
    g.recent = number(r, "recent");
    // Artwork itself lives in cache/art/<id>.image. Never persist expiring
    // signed URLs.
    games.push_back(std::move(g));
  }
  return games;
}
void save(const fs::path &file, const std::vector<meta::Game> &games) {
  Json rows = Json::array();
  for (auto &g : games)
    rows.push_back({{"id", g.id},
                    {"name", g.name},
                    {"description", g.description},
                    {"publisher", g.publisher},
                    {"platform", g.platform},
                    {"canonical", g.canonical},
                    {"package", g.package},
                    {"owned", g.owned},
                    {"system", g.system},
                    {"accountId", g.ownership_account},
                    {"recent", g.recent}});
  paths::atomic_write(
      file,
      Json{{"version", 1}, {"savedAt", paths::timestamp()}, {"games", rows}}
          .dump(2));
}
rift::Game record_install(const fs::path &data, const meta::Plan &plan, const fs::path &directory) {
  rift::Game g;
  try {
    g = rift::load(data, plan.package);
  } catch (...) {
  }
  if (g.source != "meta")
    g = {};
  g.slug = plan.package;
  if (g.name.empty())
    g.name = plan.name;
  g.app_id = plan.app_id;
  g.app_key = plan.canonical.empty() ? plan.package : plan.canonical;
  g.directory = directory;
  g.version = plan.version;
  g.binary_id = plan.binary_id;
  g.version_code = plan.version_code;
  g.source = "meta";
  // Meta's own platform calls are answered locally (the shim), as before.
  g.platform_shim = true;
  g.platform_offline = true;
  auto launch = plan.launch_file;
  std::ranges::replace(launch, '\\', '/');
  auto exe = rift::best_windows_executable(directory, paths::from_utf8(launch));
  g.executable = exe.lexically_relative(fs::weakly_canonical(directory)).generic_string();
  g.arguments = rift::split_arguments(plan.launch_parameters);
  if (rift::is_unreal_shipping(exe) &&
      std::ranges::none_of(g.arguments, [](std::string a) {
        std::ranges::transform(a, a.begin(), [](unsigned char c) { return char(std::tolower(c)); });
        return a == "-vr" || a == "-oculus" || a == "-openxr" || a == "-steamvr";
      }))
    g.arguments.push_back("-vr");
  if (g.store_url.empty())
    g.store_url = "https://www.meta.com/experiences/pcvr/" + plan.app_id + "/";
  rift::save(data, g);
  return g;
}
void uninstall(const fs::path &data, const Install &install) {
  auto current = rift::load(data, install.game.slug);
  if (current.directory != install.game.directory || current.source != install.game.source)
    throw std::runtime_error("Installation changed. Rescan before uninstalling.");
  if (current.source == "meta") {
    // Only a folder RiftLift downloaded: named for the game and holding Meta's
    // manifest, never a link out of it.
    auto target = current.directory;
    if (paths::utf8(target.filename()) != current.slug || !target.has_parent_path() ||
        target.parent_path() == target || fs::is_symlink(target))
      throw std::runtime_error("Cannot uninstall: the game folder is not one RiftLift created.");
    std::error_code ec;
    if (fs::exists(target, ec)) {
      if (!fs::exists(target / "manifest.json", ec) && !fs::exists(target / ".riftlift-download", ec))
        throw std::runtime_error("Cannot uninstall: the game folder has no Meta manifest.");
      remove_tree(target.parent_path(), target);
    }
  }
  std::error_code ec;
  fs::remove_all(rift::state_dir(data) / "artwork" / paths::from_utf8(current.slug), ec);
  rift::remove_record(data, current.slug);
}
std::vector<std::string> owning_accounts(const std::vector<meta::Game> &games,
                                         const meta::AccountIndex &accounts,
                                         std::string_view app_id) {
  std::vector<std::string> keys;
  for (const auto &account : accounts.accounts)
    if (std::ranges::any_of(games, [&](const auto &g) {
          return g.id == app_id && g.owned &&
                 g.ownership_account == account.account_id;
        }))
      keys.push_back(account.key);
  return keys;
}
LaunchChoice launch_choice(const meta::AccountIndex &accounts,
                           const std::vector<std::string> &owners,
                           std::string_view requested) {
  auto usable = [&](std::string_view key) {
    auto account = accounts.find(key);
    return account && account->signed_in && !account->needs_reauth;
  };
  if (!requested.empty())
    return {usable(requested) ? LaunchStatus::ready : LaunchStatus::reconnect,
            std::string(requested)};
  for (const auto &owner : owners)
    if (usable(owner))
      return {LaunchStatus::ready, owner};
  if (!accounts.active.empty() && usable(accounts.active))
    return {LaunchStatus::ready, accounts.active};
  return {LaunchStatus::ready, {}};
}
std::string runtime_failure(std::string_view tail, int code) {
  std::string_view last, last_runtime;
  while (!tail.empty()) {
    auto end = tail.find('\n');
    auto line = tail.substr(0, end);
    tail = end == tail.npos ? std::string_view{} : tail.substr(end + 1);
    // log::write: "<seconds>.<ms> <thread> <level> [<tag>] <message>"
    auto marker = line.find(" E [");
    if (marker == line.npos)
      continue;
    auto close = line.find("] ", marker + 4);
    if (close == line.npos)
      continue;
    auto message = line.substr(close + 2);
    while (!message.empty() && (message.back() == '\r' || message.back() == ' '))
      message.remove_suffix(1);
    if (message.empty())
      continue;
    last = message;
    if (line.substr(marker + 4, close - marker - 4) == "runtime")
      last_runtime = message;
  }
  // The runtime reports a failed launch as "[runtime] <reason>".
  if (!last_runtime.empty())
    return std::string(last_runtime);
  // Anything else gets the exit code (the game's own, passed through); the
  // last error line is context only.
  auto message =
      code < 0 || code > 255
          ? std::format("The game stopped unexpectedly (exit code 0x{:08X}).",
                        static_cast<std::uint32_t>(code))
          : std::format("The game stopped unexpectedly (exit code {}).", code);
  if (!last.empty())
    message += std::format(" Last error: {}", last);
  return message;
}
void rotate_log(const fs::path &log) {
  std::error_code ec;
  if (!fs::exists(log, ec))
    return;
  auto previous = log;
  previous.replace_extension(".prev" + log.extension().string());
  fs::rename(log, previous, ec);
  if (ec) {
    // Still open (an orphaned runtime): at least drop the old contents.
    log::warn("launcher", "Could not move aside {}: {}", paths::utf8(log),
              ec.message());
    fs::resize_file(log, 0, ec);
  }
}
bool Settings::runtime_override() const {
  return runtime.lexically_normal() !=
         defaults({}).runtime.lexically_normal();
}
std::vector<fs::path> Settings::roots() const {
  std::vector<fs::path> result{games};
  for (const auto &folder : libraries)
    if (std::ranges::find(result, folder) == result.end())
      result.push_back(folder);
  return result;
}
Settings Settings::defaults(const fs::path &data) {
  Settings s;
#ifdef _WIN32
  s.games = data / "games";
  s.runtime = paths::executable().parent_path() / "riftlift-runtime.exe";
#else
  // RiftLift has always installed to ~/Games/RiftLift.
  auto value = [](const char *name) {
    const char *text = environment::getenv(name);
    return paths::from_utf8(text ? text : "");
  };
  auto override_dir = value("RIFTLIFT_GAMES_DIR"), home = value("HOME");
  s.games = override_dir.is_absolute() ? override_dir
            : home.is_absolute()       ? home / "Games" / "RiftLift"
                                       : data / "games-downloads";
  s.runtime = paths::executable().parent_path() / "riftlift-runtime";
#endif
  return s;
}
Settings Settings::load(const fs::path &data) {
  auto s = defaults(data);
  if (!fs::exists(data / "settings.json"))
    return s;
  auto j = paths::read_json(data / "settings.json");
  auto games = paths::from_utf8(str(j, "gamesDirectory")),
       runtime = paths::from_utf8(str(j, "runtimeExecutable"));
  if (games.is_absolute())
    s.games = games.lexically_normal();
  // A saved runtime that no longer exists (moved build, old install) falls back
  // to the one shipped beside the launcher.
  if (runtime.is_absolute() && fs::exists(runtime))
    s.runtime = runtime.lexically_normal();
  s.log_level = int(std::min<uint64_t>(4, number(j, "logLevel")));
  if (!j.contains("logLevel"))
    s.log_level = 2;
  if (const auto size = number(j, "interfaceSize"); size >= 100 && size <= 200)
    s.interface_size = int(size);
  s.console = field(j, "launchWithConsole") == true;
  s.check_updates = field(j, "checkForUpdates") != false;
  s.dismissed_update = str(j, "dismissedUpdate");
  s.add_games_to_steam = field(j, "addGamesToSteam") != false;
  s.steam_add_new = field(j, "steamAddNew") != false;
  s.install_ask = field(j, "installAsk") != false;
  if (auto language = str(j, "language"); language == "en" || language == "fr")
    s.language = language;
  if (const auto &folders = field(j, "libraryFolders"); folders.is_array())
    for (const auto &folder : folders)
      if (auto path = folder.is_string() ? paths::from_utf8(folder.get<std::string>()) : fs::path();
          path.is_absolute() && path.lexically_normal() != s.games)
        s.libraries.push_back(path.lexically_normal());
  if (const auto &skip = field(j, "steamSkip"); skip.is_array())
    for (const auto &id : skip)
      if (id.is_string())
        s.steam_skip.insert(id.get<std::string>());
  return s;
}
void Settings::save(const fs::path &data) const {
  if (!games.is_absolute() || !runtime.is_absolute())
    throw std::runtime_error(
        "Use absolute paths for the games folder and runtime executable.");
  fs::create_directories(games);
  Json j{{"version", 1},
         {"gamesDirectory", paths::utf8(games)},
         {"logLevel", log_level},
         {"interfaceSize", interface_size},
         {"launchWithConsole", console},
         {"checkForUpdates", check_updates},
         {"dismissedUpdate", dismissed_update},
         {"addGamesToSteam", add_games_to_steam},
         {"steamAddNew", steam_add_new},
         {"installAsk", install_ask},
         {"language", language},
         {"steamSkip", steam_skip}};
  if (!libraries.empty()) {
    Json folders = Json::array();
    for (const auto &folder : libraries)
      folders.push_back(paths::utf8(folder));
    j["libraryFolders"] = std::move(folders);
  }
  // The bundled runtime is never pinned: a later launcher update brings its own.
  if (runtime_override())
    j["runtimeExecutable"] = paths::utf8(runtime);
  paths::atomic_write(data / "settings.json", j.dump(2));
}
} // namespace riftlift::library
