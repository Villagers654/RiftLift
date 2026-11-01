#pragma once
// The launcher without its window: accounts, the Meta library, installs,
// local and Steam games, playtime and game launches. The GUI (launcher/app.h) and the
// command line (launcher/cli.cpp) both drive this one implementation.
//
// Single-threaded like a UI: every method runs on the owner's thread. Network
// and disk work runs on workers whose completions apply in tick(); wake() tells
// the owner that completions are ready.
#include "common/download/manager.h"
#include "common/file_ops.h"
#include "common/library/library.h"
#include "common/meta/accounts.h"
#include "common/playtime.h"
#include "common/process.h"
#include "common/steam_library.h"
#include "launcher/auth_browser.h"
#include "launcher/options.h"
#include "launcher/worker.h"
#include <atomic>
#include <deque>
#include <future>
#include <map>
#include <set>
namespace riftlift::launcher {
// The message shown for the exception being handled.
std::string exception_text();
struct AccountView {
  std::string key, alias, display_name;
  bool active = false, primary = false, signed_in = false, needs_reauth = false;
};
class Core {
public:
  explicit Core(Options options);
  virtual ~Core();
  Core(const Core &) = delete;
  Core &operator=(const Core &) = delete;

  // Starts the workers, the first scan and (unless offline, a direct launch
  // or `refresh_library` is false) the library refresh. Call once the derived
  // object is fully constructed: completions call wake().
  void start(bool refresh_library = true);
  // Stops launches, downloads and workers; idempotent.
  void shutdown();
  // Applies finished background work and polls games and downloads.
  virtual void tick();

  const std::vector<AccountView> &accounts() const { return accounts_; }
  std::vector<std::string> game_owners(std::string_view app_id) const;
  library::LaunchChoice launch_choice(std::string_view app_id,
                                      std::string_view account = {}) const;
  void add_account();
  void remove_account(std::string_view key, bool delete_saves = false);
  void sign_out_account(std::string_view key);
  void switch_account(std::string_view key);
  void mark_primary(std::string_view key);
  void play_as(std::string_view app_id, std::string_view key);
  // The owning account a Play/Install on this game uses: the user's pick, else
  // the active account if it owns the game, else the first usable owner.
  std::string owner_for(std::string_view app_id) const;
  // The account Play asks for: the one picked with Play as, else none, so
  // launch_choice takes a usable owner, the active account, or no account.
  std::string play_key(std::string_view app_id) const;
  const AccountView *account(std::string_view key) const;
  // Adds a game installed elsewhere by its .exe; returns its app id (slug).
  std::string add_local_game(const fs::path &executable, std::string name = {},
                             std::string_view arguments = {});
  // Installed Steam games with an Oculus mode (scanned on a worker; the GUI
  // lists them), and adding one.
  void scan_steam_games();
  std::string add_steam_game(const rift::Game &game);
  // A game's launch settings (launch options, DLL overrides, environment).
  void save_game_settings(const rift::Game &game);
  playtime::Record playtime_of(std::string_view app_id) const;
  // Writes a redacted debug report about this game's last launch (or the
  // most recent launch when empty) and returns the file.
  fs::path save_debug_report(std::string_view app_id, std::string_view version) const;
  // Uploads a saved report in the background; `done` runs on the UI thread
  // with its link, or with why the upload failed.
  void share_debug_report(fs::path file, std::function<void(std::string url, std::string error)> done);

protected:
  // Completions are ready for tick(); called from worker threads.
  virtual void wake() {}
  // A library refresh started (the GUI retries failed artwork).
  virtual void on_refresh() {}
  // An install or import was queued (the GUI shows Downloads).
  virtual void on_install_queued() {}

  Options options_;
  fs::path data_;
  SecretStore secrets_;
  meta::AccountVault vault_;
  meta::AccountIndex account_index_;
  std::vector<AccountView> accounts_;
  std::string account_key_;
  void reload_accounts();
  // Refreshes the libraries of the signed-in accounts other than the active
  // one, so every owned game has its store metadata and owner.
  void refresh_accounts();
  std::set<std::string> library_pending_;
  // Public store listings for visible games still missing their name,
  // description or publisher (anonymous; no account token is sent).
  void fill_metadata();
  std::set<std::string> metadata_tried_;
  bool metadata_busy_ = false;
  library::Settings settings_;
  std::unique_ptr<Worker> worker_;
  std::unique_ptr<download::Manager> downloads_;
  AuthBrowser browser_;
  std::shared_ptr<meta::Auth> auth_;
  std::optional<Session> session_;
  std::string account_scope_;
  uint64_t epoch_ = 0;
  std::vector<meta::Game> games_;
  std::vector<library::Install> installs_;
  std::map<std::string, meta::Details> details_;
  std::map<std::string, std::string> detail_errors_;
  std::set<std::string> details_pending_;
  std::map<std::string, std::string> owner_pick_; // app id -> account key
  struct Running {
    Process process;
    fs::path log,     // the runtime's own log (--log)
        console;      // its stdout and stderr
    std::string tail;
    std::optional<int> exit;
    std::string failure; // shown for a non-zero exit
    bool quitting = false; // Quit game was pressed; the game is closing
    std::unique_ptr<playtime::Session> played;
  };
  std::map<std::string, Running> running_;
  std::set<std::string> launch_pending_;
  // Cross-process game lock also covers a Steam launch beside an open UI.
  std::map<std::string, std::unique_ptr<FileLock>> game_locks_;
  bool steam_pending_ = false, steam_busy_ = false, steam_restart_ = false;
  bool steam_sync_allowed_ = false, steam_scan_reliable_ = false;
  size_t steam_games_ = 0;
  uint64_t steam_poll_ = 0;
  std::atomic<uint64_t> steam_generation_{0};
  void sync_steam();
  void restart_steam();
  // A new game the GUI asks about before it installs: where it goes and
  // whether it goes to Steam (app id, name).
  std::optional<std::pair<std::string, std::string>> install_ask_;
  // Adds a game to Steam or leaves it out (saved), then syncs.
  void set_in_steam(std::string_view app_id, bool in);
  bool in_steam(std::string_view app_id) const { return !settings_.steam_skip.contains(std::string(app_id)); }
  void launch_game(std::string_view app_id, std::string_view account);
  std::string selected_, notice_, warning_, auth_status_;
  bool library_partial_ = false;  // Meta did not confirm the whole library
  bool refreshing_ = false, scanning_ = false, signing_in_ = false,
       verifying_ = false;
  bool auth_page_loaded_ = false; // the Meta sign-in page finished loading
  std::string refresh_error_;     // last library refresh
  std::string owned_link_;        // store link of a game to add when Meta cannot list the library
  bool adding_ = false;
  size_t last_completed_ = 0;
  std::uint64_t last_discarded_ = 0;
  uint64_t last_poll_ = 0;
  // Steam games with an Oculus mode, from the last scan.
  std::vector<rift::Game> steam_games_found_;
  bool steam_scanning_ = false;
  void refresh();
  // Adds the game at owned_link_ once Meta confirms this account owns it.
  void add_owned();
  void rescan();
  void select(std::string id);
  void sign_in();
  void sign_out();
  void play(const meta::Game &game);
  // Installs or updates a game. An installed game updates where it is; a new
  // one goes to `root` (a library folder, added if new), else the games folder.
  void install(const meta::Game &game, const fs::path &root = {});
  // The Install button: downloads belong to the active account, so a game
  // that only another signed-in account owns switches to that account first.
  void install_for_owner(const meta::Game &game, const fs::path &root = {});
  // The library folder an installed game is in.
  static fs::path root_of(const library::Install &install) {
    return install.directory.parent_path();
  }
  // Removes an installed game's files; saves stay.
  void uninstall(const library::Install &install);
  // Saves settings and rescans the library folders they name.
  void apply_settings(library::Settings next, bool announce = true);
  void save_library();
  void action(const std::function<void()> &fn);
  void error(std::string message);
  const library::Install *installed(std::string_view id,
                                    bool complete = true) const;
  // An installation by its slug, whatever app id it has.
  const library::Install *installed_package(std::string_view package) const {
    for (const auto &i : installs_)
      if (i.plan.package == package) return &i;
    return nullptr;
  }
  meta::Game *game(std::string_view id);
  std::vector<meta::Game> visible_games() const;
  // Meta lists a newer build than the installed one (needs the game's details).
  bool update_available(std::string_view id) const;

  // Whether this process keeps the persistent download queue (see start()).
  bool owns_download_queue() const { return queue_lock_ != nullptr; }

private:
  bool started_ = false, stopped_ = false;
  std::unique_ptr<FileLock> queue_lock_;
};
} // namespace riftlift::launcher
