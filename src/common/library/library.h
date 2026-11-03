#pragma once
#include "common/meta/store.h"
#include "common/meta/accounts.h"
#include "common/playtime.h"
#include "common/rift/game.h"
#include <set>
namespace riftlift::library {
// A game with a record in <data>/games: a store install, a local game or a
// Steam game. `plan` is the record's identity in the launcher's terms: its
// app_id is Meta's id for store installs and the slug for the others.
struct Install {
  meta::Plan plan;
  rift::Game game;
  fs::path directory;
  bool complete = false;
  uint64_t bytes = 0;
  std::string label;  // the record's own name, shown when Meta has no listing
  playtime::Record played;  // as of the scan
};
Install from_record(const rift::Game &game);
// Every game record. `authoritative` is false when one could not be read;
// such a scan must never drive removal of Steam shortcuts.
std::vector<Install> scan(const fs::path &data, bool *authoritative = nullptr);
// Only the current account may reconcile its own previously confirmed titles.
void merge(std::vector<meta::Game> &games, const meta::Snapshot &snapshot,
           std::string_view account);
std::vector<meta::Game> load(const fs::path &file);
void save(const fs::path &file, const std::vector<meta::Game> &games);
// Writes (or updates) the record of a store build just assembled in
// `directory`: its launch executable and arguments, keeping the user's own
// launch settings from an earlier record.
// `pinned`: a version chosen by hand, kept beside the game's other versions.
rift::Game record_install(const fs::path &data, const meta::Plan &plan, const fs::path &directory,
                          bool pinned = false);
// The library id of a chosen version ("<app id>@<binary id>"), and the app id
// of a library id (the id itself for anything else).
std::string version_id(std::string_view app_id, std::string_view binary_id);
std::string_view base_app(std::string_view id);
// Removes the game's record; a store install's files go too, never a local or
// Steam game's.
void uninstall(const fs::path &data, const Install &install);
std::vector<std::string> owning_accounts(const std::vector<meta::Game> &games,
                                         const meta::AccountIndex &accounts,
                                         std::string_view app_id);
enum class LaunchStatus { ready, reconnect };
struct LaunchChoice {
  LaunchStatus status = LaunchStatus::ready;
  std::string account_key;  // empty: no account
};
// The account a game launches as: an explicitly requested one must be signed
// in; otherwise a signed-in owner, else the active account, else none. Rift
// games need no account to run; it only names whose playtime and logs these are.
LaunchChoice launch_choice(const meta::AccountIndex &accounts,
                           const std::vector<std::string> &owners,
                           std::string_view requested = {});
// What the launcher shows for a runtime that exited with `exit_code`: the
// "[runtime]" error line the runtime writes for a failed launch, found in the
// tail of its log, else the exit code with the last error line as context.
std::string runtime_failure(std::string_view log_tail, int exit_code);
// Before a launch: moves the previous run's log to <stem>.prev.log (replacing
// an older one), so the runtime, which appends, starts a log of this run only.
void rotate_log(const fs::path &log);
// The Platform error (common/meta/platform.h) the last entitlement check of a
// run's game got, as riftlift-runtime's relay logged it; 0 when it passed or
// the game made none.
int entitlement_error(const fs::path &log);
struct Settings {
  // `runtime` is the bundled runtime beside the launcher unless the user set
  // another one; only such an override is saved.
  fs::path games, runtime;
  int log_level = 2;
  bool console = false;
  // Linux: games also keep Proton, graphics, crash and XRizer logs for
  // debug reports (slower; RIFTLIFT_PROTON_LOG for the runtime).
  bool debug_logging = false;
  // The launcher asks GitHub for a newer release at start; "Not now" skips that version.
  bool check_updates = true;
  std::string dismissed_update;
  bool add_games_to_steam = true;
  // A newly installed game goes to Steam unless the user opts out: the
  // default, and whether installing asks (a checkbox in the GUI). Games left
  // out are listed by app id.
  bool steam_add_new = true;
  std::set<std::string> steam_skip;
  // Starting a new game's install asks where it goes and whether it goes to
  // Steam (a dialog in the GUI); off installs to `games` with the defaults.
  bool install_ask = true;
  // Other folders new games were installed to; downloads go to `games`
  // unless one is picked.
  std::vector<fs::path> libraries;
  // Launcher interface size in percent of the display's scale.
  int interface_size = 100;
  // "auto" follows the system; otherwise a language code (en, fr).
  std::string language = "auto";
  // Games that download at once (Settings > Library); one by default.
  int parallel_downloads = 1;
  bool runtime_override() const;
  // `games` first, then the other library folders.
  std::vector<fs::path> roots() const;
  static Settings defaults(const fs::path &data);
  static Settings load(const fs::path &data);
  void save(const fs::path &data) const;
};
} // namespace riftlift::library
