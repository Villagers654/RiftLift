#include "launcher/core.h"
#include "launcher/debug_report.h"
#include "launcher/diagnosis.h"
#include "common/environment.h"
#include "common/file_ops.h"
#include "common/http/client.h"
#include "common/log.h"
#include "common/meta/platform.h"
#include "common/rift/detection.h"
#include "common/rift/local_games.h"
#include "launcher/i18n.h"
#include "launcher/sign_in_link.h"
#include <chrono>
#include <fstream>

namespace riftlift::launcher {
namespace {
uint64_t now_ms() {
  return uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(
                      std::chrono::steady_clock::now().time_since_epoch())
                      .count());
}
// Where the Python releases (0.11 and older) kept meta-accounts.json.
fs::path python_config_dir(const fs::path &data) {
#ifdef _WIN32
  return data / "config";
#else
  (void)data;
  const char *xdg = environment::getenv("XDG_CONFIG_HOME");
  if (xdg && *xdg && fs::path(xdg).is_absolute())
    return fs::path(xdg) / "riftlift";
  const char *home = environment::getenv("HOME");
  return home && *home ? fs::path(home) / ".config/riftlift" : fs::path();
#endif
}
// Adds the Python release's sign-ins to the vault once, then renames its file
// (kept as meta-accounts.json.imported, so going back to that release still
// works). Accounts saved without a numeric Meta id are looked up online, so
// offline the file waits for the next start.
void import_python_accounts(meta::AccountVault &vault, const fs::path &config, bool offline) {
  const auto file = config / "meta-accounts.json";
  std::error_code ec;
  if (config.empty() || !fs::is_regular_file(file, ec))
    return;
  const auto stored = paths::read_json(file);
  const auto &entries = field(stored, "accounts");
  bool complete = true;
  int imported = 0;
  for (const auto &entry : entries.is_array() ? entries : Json::array()) {
    Session s;
    s.access_token = str(entry, "token");
    s.account_id = str(entry, "id");
    s.alias = s.display_name = str(entry, "name");
    s.source = "riftlift-python";
    if (s.access_token.empty())
      continue;
    const bool numeric = !s.account_id.empty() && std::ranges::all_of(s.account_id, [](unsigned char c) {
      return std::isdigit(c);
    });
    try {
      if (!numeric) {
        if (offline) {
          complete = false;
          continue;
        }
        auto identity = meta::Store(s.access_token).identity();
        s.account_id = identity.account_id;
        s.alias = identity.alias;
        s.display_name = identity.display_name;
      }
      vault.upsert(s);
      ++imported;
    } catch (const std::exception &e) {
      // Not retried: a token Meta no longer accepts never will again.
      log::warn("accounts", "Could not import a sign-in from the previous RiftLift: {}", e.what());
    }
  }
  if (complete)
    fs::rename(file, config / "meta-accounts.json.imported", ec);
  if (imported)
    log::info("accounts", "Imported {} sign-ins from the previous RiftLift.", imported);
}
} // namespace
std::string exception_text() {
  try {
    throw;
  } catch (const Json::exception &) {
    return "Unexpected response data. Refresh and try again.";
  } catch (const fs::filesystem_error &) {
    return "Could not access local files. Check the selected folder and "
           "permissions.";
  } catch (const std::exception &e) {
    return e.what();
  } catch (...) {
    return "An unexpected operation failed. Try again.";
  }
}
Core::Core(Options options)
    : options_(std::move(options)), data_(paths::data_dir()), secrets_(data_),
      vault_(data_), settings_(library::Settings::defaults(data_)) {
  fs::create_directories(data_ / "logs");
  log::set_file(paths::utf8(data_ / "logs" / (options_.launch_requested ?
      "launcher-direct-" + std::to_string(current_process_id()) + ".log" :
      options_.cli ? "cli-" + std::to_string(current_process_id()) + ".log" : "launcher.log")));
  if (options_.cli)
    log::info("launcher", "RiftLift command line starting.");
  else
    log::info("launcher", "RiftLift native launcher starting.");
  steam_sync_allowed_ = options_.interactive() && steam_library::profile_sync_allowed();
  if (!options_.interactive())
    log::info("steam", "Steam sync disabled for non-interactive launcher mode.");
  else if (!steam_sync_allowed_)
    log::info("steam", "Steam sync disabled: redirected data profile has no Steam root override.");
  action([&] { settings_ = library::Settings::load(data_); });
  log::set_level(static_cast<log::Level>(settings_.log_level));
  action([&] {
    auto migration = vault_.migrate_v1(
        options_.offline ? std::function<Session(Session)>{}
                         : std::function<Session(Session)>{[](Session s) {
                             auto snapshot =
                                 meta::Store(s.access_token).identity();
                             s.account_id = snapshot.account_id;
                             s.alias = snapshot.alias;
                             s.display_name = snapshot.display_name;
                             return s;
                           }});
    // Only for the real profile: a redirected one never picks up these sign-ins.
    if (!environment::getenv("RIFTLIFT_DATA_DIR"))
      import_python_accounts(vault_, python_config_dir(data_), options_.offline);
    account_index_ = vault_.reconcile();
    reload_accounts();
    account_key_ = account_index_.active;
    if (!account_key_.empty())
      session_ = vault_.load(account_key_);
    if (migration == meta::Migration::needs_identity) {
      session_ = secrets_.load();
      warning_ = "Legacy account mode: connect to Meta to finish account "
                 "migration. Your original credentials are retained.";
    }
    if (session_)
      account_scope_ = session_->account_id;
    // The old persistent WebView cookie store is never reused by InPrivate
    // sign-in.
    remove_tree(data_, data_ / "auth-webview");
  });
  action([&] {
    games_ = library::load(data_ / "library.json");
    save_library();
  });
}
Core::~Core() { shutdown(); }
void Core::start(bool refresh_library) {
  if (started_)
    return;
  started_ = true;
  // A sign-in that never ended (a crash) still holds the oculus:// handler.
  if (!options_.cli && !options_.automation)
    try {
      sign_in_link::restore(data_);
    } catch (...) {
    }
  worker_ = std::make_unique<Worker>([this] { wake(); });
  download::Manager::Services services;
  // A finished build becomes (or updates) the game's record.
  services.installed = [data = data_](const download::Installed &done) {
    library::record_install(data, done.plan, done.directory, done.pinned);
  };
  // One process at a time keeps the persistent download queue: a second
  // launcher or the command line beside an open launcher would download the
  // same jobs twice. A direct launch never keeps it.
  bool persistent = !options_.launch_requested;
  if (persistent)
    try {
      queue_lock_ = std::make_unique<FileLock>(data_ / "locks" / "download-queue.lock", false);
    } catch (...) {
      if (options_.cli)
        persistent = false;
      else
        log::warn("launcher", "Another RiftLift process holds the download queue.");
    }
  downloads_ = std::make_unique<download::Manager>(data_, [this] { wake(); },
      std::move(services), persistent);
  downloads_->set_account(account_key_);
  downloads_->set_parallel(unsigned(settings_.parallel_downloads));
  rescan();
  if (refresh_library && session_ && !options_.offline && !options_.launch_requested)
    refresh();
}
bool Core::update_available(std::string_view id) const {
  const auto *local = installed(id);
  auto detail = details_.find(std::string(id));
  if (!local || detail == details_.end())
    return false;
  auto live = number(Json(detail->second.version_code)), current = number(Json(local->plan.version_code));
  return live && current ? live > current
                         : (!detail->second.live_id.empty() && detail->second.live_id != local->plan.binary_id);
}
void Core::shutdown() {
  if (stopped_)
    return;
  stopped_ = true;
  ++epoch_;
  browser_.close();
  if (link_handler_ == 1)
    try {
      sign_in_link::restore(data_);
    } catch (...) {
    }
  for (auto &[id, r] : running_)
    if (r.played)
      try {
        r.played->close();
      } catch (...) {
      }
  running_.clear();
  downloads_.reset();
  queue_lock_.reset();
  if (worker_)
    worker_->stop();
  worker_.reset();
}
void Core::uninstall(const library::Install &install) {
  std::unique_ptr<FileLock> lock;
  try {
    lock = std::make_unique<FileLock>(data_ / "locks" / ("game-" + install.plan.package + ".lock"), false);
  } catch (...) {
    throw std::runtime_error("Close this game before uninstalling it.");
  }
  library::uninstall(data_, install);
  rescan();
}
void Core::apply_settings(library::Settings next, bool announce) {
  // Games already installed stay where they are: a games folder that holds
  // some stays a library folder.
  if (next.games != settings_.games &&
      std::ranges::find(next.libraries, settings_.games) == next.libraries.end() &&
      std::ranges::any_of(installs_, [&](auto &i) { return root_of(i) == settings_.games; }))
    next.libraries.push_back(settings_.games);
  std::erase(next.libraries, next.games);
  next.save(data_);
  settings_ = std::move(next);
  if (downloads_) downloads_->set_parallel(unsigned(settings_.parallel_downloads));
  log::set_level(static_cast<log::Level>(settings_.log_level));
  if (announce)
    notice_ = "Settings saved.";
  rescan();
}
void Core::action(const std::function<void()> &fn) {
  try {
    fn();
  } catch (...) {
    error(exception_text());
  }
}
void Core::error(std::string message) {
  notice_ = std::move(message);
  log::warn("launcher", "{}", notice_);
}
void Core::save_library() {
  if (options_.launch_requested) return; // The visible UI owns the shared catalog.
  library::save(data_ / "library.json", games_);
  if (worker_ && !scanning_ && !options_.launch_requested) sync_steam();
}
void Core::reload_accounts() {
  account_index_ = vault_.index();
  accounts_.clear();
  for (const auto &a : account_index_.accounts) {
    AccountView view{a.key,
                     {},
                     {},
                     a.key == account_index_.active,
                     a.key == account_index_.primary,
                     a.signed_in,
                     a.needs_reauth};
    if (a.signed_in && !a.needs_reauth) {
      try {
        if (auto s = vault_.load(a.key)) {
          view.alias = s->alias;
          view.display_name = s->display_name;
        } else
          view.needs_reauth = true;
      } catch (...) {
        view.needs_reauth = true;
      }
    }
    accounts_.push_back(std::move(view));
  }
}
void Core::refresh_accounts() {
  if (options_.offline || options_.launch_requested)
    return;
  for (const auto &a : accounts_) {
    if (a.key == account_key_ || !a.signed_in || a.needs_reauth ||
        library_pending_.contains(a.key))
      continue;
    std::optional<Session> stored;
    try {
      stored = vault_.load(a.key);
    } catch (...) {
      continue;
    }
    if (!stored || stored->account_id.empty())
      continue;
    auto key = a.key, token = stored->access_token, account = stored->account_id;
    library_pending_.insert(key);
    worker_->submit([this, key, token, account](std::stop_token stop) -> Worker::Completion {
      try {
        auto snapshot = meta::Store(token, stop).library();
        return [this, key, account, snapshot = std::move(snapshot)] {
          library_pending_.erase(key);
          if (!snapshot.account_id.empty() && snapshot.account_id != account) {
            log::warn("library", "Skipped a library refresh that returned a different account.");
            return;
          }
          action([&] {
            library::merge(games_, snapshot, account);
            vault_.update_profile(key, snapshot.alias, snapshot.display_name);
            reload_accounts();
            save_library();
            log::info("library", "Refreshed {} Rift titles for another account; complete={}",
                      snapshot.games.size(), snapshot.complete);
            fill_metadata();
          });
        };
      } catch (...) {
        auto e = exception_text();
        return [this, key, e] {
          library_pending_.erase(key);
          log::warn("library", "Could not refresh another account's library: {}", e);
          fill_metadata();
        };
      }
    });
  }
}
// Installed games only: the library grid shows their publisher, and a game
// page fetches its own details when opened. Runs after the account refreshes,
// which bring most of this, so it fills only what they left out.
void Core::fill_metadata() {
  if (options_.offline || options_.launch_requested || metadata_busy_ ||
      refreshing_ || !library_pending_.empty())
    return;
  // An owner's token reads the listing from Meta's graph like a game page
  // does; games no signed-in account owns fall back to the public store page.
  std::vector<std::pair<std::string, std::string>> wanted; // app id, token
  std::map<std::string, std::string> tokens;               // account key -> token
  for (const auto &i : installs_) {
    const auto &id = i.plan.app_id;
    const auto *g = game(id);
    if ((g && !g->description.empty() && !g->publisher.empty()) ||
        metadata_tried_.contains(id))
      continue;
    metadata_tried_.insert(id);
    std::string token;
    // Local and Steam games have no Meta listing (keyed by slug).
    if (!digits(id, 5))
      continue;
    for (const auto &key : game_owners(id)) {
      if (!tokens.contains(key))
        try {
          auto stored = vault_.load(key);
          tokens[key] = stored ? stored->access_token : std::string();
        } catch (...) {
          tokens[key].clear();
        }
      if (!tokens[key].empty()) {
        token = tokens[key];
        break;
      }
    }
    wanted.emplace_back(id, std::move(token));
  }
  if (wanted.empty())
    return;
  metadata_busy_ = true;
  worker_->submit([this, wanted](std::stop_token stop) -> Worker::Completion {
    std::vector<meta::Game> found;
    for (const auto &[id, token] : wanted) {
      if (stop.stop_requested())
        break;
      try {
        found.push_back(token.empty() ? meta::Store({}, stop).artwork(id)
                                      : meta::Store(token, stop).details(id).game);
      } catch (...) {
        log::warn("library", "No public store listing for application {}: {}", id,
                  exception_text());
      }
    }
    return [this, found] {
      metadata_busy_ = false;
      action([&] {
        for (const auto &listing : found) {
          bool known = false;
          for (auto &g : games_)
            if (g.id == listing.id) {
              known = true;
              if (g.description.empty())
                g.description = listing.description;
              if (g.publisher.empty())
                g.publisher = listing.publisher;
            }
          // Installed games no signed-in account owns still get their store
          // name; the entry asserts no ownership.
          if (!known && !listing.name.empty()) {
            auto g = listing;
            g.owned = false;
            g.ownership_account.clear();
            g.image.clear();
            games_.push_back(std::move(g));
          }
        }
        if (!found.empty())
          save_library();
      });
      fill_metadata(); // games that appeared meanwhile
    };
  });
}
std::vector<std::string> Core::game_owners(std::string_view app_id) const {
  // A chosen version belongs to whoever owns the game.
  return library::owning_accounts(games_, account_index_, library::base_app(app_id));
}
library::LaunchChoice Core::launch_choice(std::string_view app_id,
                                         std::string_view account) const {
  return library::launch_choice(account_index_, game_owners(app_id), account);
}
void Core::add_account() {
  if (std::ranges::count(account_index_.accounts, true,
                         &meta::Account::signed_in) >= 4)
    throw std::runtime_error(
        "Sign out an account before adding another (maximum four).");
  sign_in();
}
void Core::switch_account(std::string_view key) {
  auto loaded = vault_.load(key);
  if (!loaded)
    throw std::runtime_error("Reconnect this account before switching.");
  vault_.set_active(key);
  ++epoch_;
  browser_.close();
  auth_.reset();
  signing_in_ = verifying_ = refreshing_ = false;
  details_.clear();
  detail_errors_.clear();
  details_pending_.clear();
  session_ = std::move(loaded);
  account_key_ = key;
  account_scope_ = session_->account_id;
  reload_accounts();
  downloads_->set_account(account_key_);
  warning_.clear();
  library_partial_ = false;
  auth_status_.clear();
  if (!options_.offline)
    refresh();
}
void Core::sign_out_account(std::string_view key) {
  if (!meta::valid_account_key(key))
    throw std::runtime_error("Invalid account key.");
  const bool active = account_key_ == key;
  downloads_->cancel_account(key);
  vault_.sign_out(key);
  if (active) {
    ++epoch_;
    browser_.close();
    auth_.reset();
    session_.reset();
    account_key_.clear();
    account_scope_.clear();
    signing_in_ = verifying_ = refreshing_ = false;
    details_.clear();
    details_pending_.clear();
    detail_errors_.clear();
    warning_.clear();
    library_partial_ = false;
    auth_status_.clear();
    downloads_->set_account({});
  }
  reload_accounts();
  notice_ =
      "Account signed out. Its installed games and saves remain on this PC.";
}
void Core::remove_account(std::string_view key, bool delete_saves) {
  std::string selected(key);
  if (!meta::valid_account_key(selected))
    throw std::runtime_error("Invalid account key.");
  for (const auto &[id, running] : running_)
    if (running.process.active())
      throw std::runtime_error(
          "Close running games before removing an account.");
  (void)delete_saves;  // Rift games keep their saves in the prefix, not per account.
  sign_out_account(selected);
  vault_.remove(selected);
  reload_accounts();
}
void Core::mark_primary(std::string_view key) {
  vault_.set_primary(key);
  reload_accounts();
}
const library::Install *Core::installed(std::string_view id,
                                       bool complete) const {
  for (auto &i : installs_)
    if (i.plan.app_id == id && (!complete || i.complete))
      return &i;
  return nullptr;
}
meta::Game *Core::game(std::string_view id) {
  for (auto &g : games_)
    if (g.id == id && g.ownership_account == account_scope_)
      return &g;
  for (auto &g : games_)
    if (g.id == id)
      return &g;
  return nullptr;
}
std::vector<meta::Game> Core::visible_games() const {
  std::vector<meta::Game> out;
  std::set<std::string> seen;
  for (auto &g : games_)
    if (g.owned && (!g.system || installed(g.id, false)) && !game_owners(g.id).empty() &&
        seen.insert(g.id).second) {
      out.push_back(g);
    }
  for (auto &i : installs_)
    if (seen.insert(i.plan.app_id).second) {
      auto old = std::ranges::find_if(
          games_, [&](auto &g) { return g.id == i.plan.app_id; });
      if (old != games_.end()) {
        out.push_back(*old);
        out.back().owned = false;
      } else {
        // The record's own name until a store listing arrives; a chosen
        // version shows its game's artwork and details.
        meta::Game g;
        if (auto base = library::base_app(i.plan.app_id); base != i.plan.app_id)
          if (auto it = std::ranges::find(games_, base, &meta::Game::id); it != games_.end()) {
            g = *it;
            g.owned = false;
          }
        g.id = i.plan.app_id;
        g.name = i.label.empty() ? i.plan.package : i.label;
        g.package = i.plan.package;
        if (!i.game.description.empty())
          g.description = i.game.description;
        if (!i.game.publisher.empty() || !i.game.developer.empty())
          g.publisher = i.game.publisher.empty() ? i.game.developer : i.game.publisher;
        g.platform = i.game.source == "steam" ? "STEAM" : i.game.source == "local" ? "LOCAL" : "PC";
        out.push_back(g);
      }
    }
  // Covers RiftLift saved with each record (wide grid art first), and the
  // last launch from playtime for "Recently played".
  for (auto &g : out)
    if (auto i = installed(g.id, false)) {
      g.recent = std::max(g.recent, i->played.last_played_unix());
      if (g.image.empty())
        for (const char *kind : {"grid", "hero", "portrait"})
          if (auto art = i->game.artwork.find(kind); art != i->game.artwork.end()) {
            g.image = art->second;
            break;
          }
    }
  return out;
}
void Core::rescan() {
  if (scanning_)
    return;
  scanning_ = true;
  steam_scan_reliable_ = false;
  ++steam_generation_;
  worker_->submit([this, data = data_](std::stop_token) -> Worker::Completion {
    try {
      bool reliable = false;
      auto result = library::scan(data, &reliable);
      return [this, result = std::move(result), reliable] {
        scanning_ = false;
        installs_ = result;
        steam_scan_reliable_ = reliable;
        if (!options_.launch_requested) sync_steam();
        log::info("library", "Found {} complete installations.",
                  std::ranges::count_if(installs_,
                                        [](auto &i) { return i.complete; }));
        fill_metadata();
      };
    } catch (...) {
      auto e = exception_text();
      return [this, e] {
        scanning_ = false;
        error(e);
      };
    }
  });
}
void Core::add_owned() {
  if (adding_ || !session_ || options_.offline)
    return;
  auto id = meta::app_id_from_link(owned_link_);
  if (id.empty()) {
    error("Paste the game's Meta Rift store link, like meta.com/experiences/pcvr/1234567890123456.");
    return;
  }
  adding_ = true;
  auto token = session_->access_token;
  auto epoch = epoch_;
  worker_->submit([this, token, epoch, id](std::stop_token stop) -> Worker::Completion {
    try {
      auto snapshot = meta::Store(token, stop).owned_listing(id);
      return [this, snapshot = std::move(snapshot), epoch] {
        if (epoch != epoch_)
          return;
        adding_ = false;
        action([&] {
          if (snapshot.games.empty())
            throw std::runtime_error("Meta has no Rift build of that game for this account.");
          if (session_ && !session_->account_id.empty() && session_->account_id != snapshot.account_id)
            throw std::runtime_error("Meta returned a different account. Sign out and reconnect.");
          library::merge(games_, snapshot, account_scope_);
          save_library();
          owned_link_.clear();
          log::info("library", "Added {} from its store listing", snapshot.games.front().id);
          notice_ = snapshot.games.front().name + " is in your library.";
          fill_metadata();
        });
      };
    } catch (...) {
      auto e = exception_text();
      return [this, epoch, e] {
        if (epoch == epoch_) {
          adding_ = false;
          error(e);
        }
      };
    }
  });
}
void Core::refresh() {
  if (options_.offline) {
    notice_ = "Offline mode. Restart without --offline to connect to Meta.";
    return;
  }
  if (refreshing_ || !session_)
    return;
  refreshing_ = true;
  refresh_error_.clear();
  notice_.clear();
  auto token = session_->access_token;
  auto epoch = epoch_;
  on_refresh();
  worker_->submit([this, token,
                   epoch](std::stop_token stop) -> Worker::Completion {
    try {
      auto snapshot = meta::Store(token, stop).library();
      return [this, snapshot = std::move(snapshot), epoch] {
        if (epoch != epoch_)
          return;
        refreshing_ = false;
        action([&] {
          if (!snapshot.account_id.empty()) {
            if (session_ && !session_->account_id.empty() &&
                session_->account_id != snapshot.account_id)
              throw std::runtime_error(
                  "Meta returned a different account. Sign out and reconnect.");
            account_scope_ = snapshot.account_id;
            if (session_)
              session_->account_id = snapshot.account_id;
          }
          library::merge(games_, snapshot, account_scope_);
          if (session_ && !snapshot.alias.empty())
            session_->alias = snapshot.alias;
          if (session_ && !snapshot.display_name.empty())
            session_->display_name = snapshot.display_name;
          if (session_) {
            if (account_key_.empty()) {
              vault_.migrate_v1([&](Session) { return *session_; });
              account_index_ = vault_.index();
              account_key_ = account_index_.active;
            } else {
              // Updating the alias must not change the launch selection
              // unexpectedly.
              vault_.upsert(*session_);
            }
            reload_accounts();
            downloads_->set_account(account_key_);
          }
          warning_ = snapshot.warning;
          library_partial_ = !snapshot.complete;
          save_library();
          log::info("library", "Refreshed {} Rift titles; complete={}",
                    snapshot.games.size(), snapshot.complete);
          refresh_accounts();
          fill_metadata();
        });
      };
    } catch (...) {
      auto e = exception_text();
      return [this, epoch, e] {
        if (epoch == epoch_) {
          refreshing_ = false;
          refresh_error_ = e;
          error(e);
          fill_metadata();
        }
      };
    }
  });
}
void Core::select(std::string id) {
  selected_ = std::move(id);
  if (!digits(selected_, 5) || !session_ || options_.offline || details_.contains(selected_) ||
      !details_pending_.insert(selected_).second)
    return;
  auto app = selected_, token = session_->access_token;
  auto epoch = epoch_;
  detail_errors_.erase(app);
  worker_->submit(
      [this, app, token, epoch](std::stop_token stop) -> Worker::Completion {
        try {
          auto d = meta::Store(token, stop).details(app);
          return [this, app, epoch, d = std::move(d)] {
            if (epoch != epoch_)
              return;
            details_pending_.erase(app);
            details_[app] = d;
            if (auto g = game(app)) {
              if (!d.game.description.empty())
                g->description = d.game.description;
              if (!d.game.publisher.empty())
                g->publisher = d.game.publisher;
              if (!d.game.image.empty())
                g->image = d.game.image;
              action([&] { save_library(); });
            }
          };
        } catch (...) {
          auto e = exception_text();
          return [this, app, epoch, e] {
            if (epoch == epoch_) {
              details_pending_.erase(app);
              detail_errors_[app] = e;
            }
          };
        }
      });
}
void Core::sign_in() {
  if (options_.offline) {
    notice_ = "Sign-in needs a connection. Restart without --offline.";
    return;
  }
  if (signing_in_)
    return;
  signing_in_ = true;
  verifying_ = false;
  auth_status_ = "Opening secure Meta sign-in...";
  notice_.clear();
  auto epoch = ++epoch_;
  refreshing_ = false;
  details_pending_.clear();
  details_.clear();
  worker_->submit([this, epoch](std::stop_token stop) -> Worker::Completion {
    try {
      auto auth = std::make_shared<meta::Auth>();
      auto url = auth->begin(stop);
      return [this, epoch, auth, url] {
        if (epoch != epoch_)
          return;
        auth_ = auth;
        auth_url_ = url;
        auth_manual_ = false;
        auth_paste_error_.clear();
        if (auth_window_)
          action([&] { browser_.open(url, data_); });
        auth_status_ = "Complete sign-in in the Meta window.";
      };
    } catch (...) {
      auto e = exception_text();
      return [this, epoch, e] {
        if (epoch == epoch_) {
          signing_in_ = false;
          auth_status_.clear();
          error(e);
        }
      };
    }
  });
}
void Core::sign_out() {
  if (!account_key_.empty()) {
    sign_out_account(account_key_);
    return;
  }
  ++epoch_;
  browser_.close();
  auth_.reset();
  downloads_->cancel_all();
  session_.reset();
  account_scope_.clear();
  signing_in_ = verifying_ = refreshing_ = false;
  details_.clear();
  details_pending_.clear();
  detail_errors_.clear();
  warning_.clear();
  library_partial_ = false;
  auth_status_.clear();
  secrets_.clear();
  notice_ = "Signed out. Installed games remain on this PC.";
}
void Core::install(const meta::Game &g, const fs::path &root) {
  if (options_.offline)
    throw std::runtime_error("Downloads are unavailable in offline mode.");
  if (!session_)
    throw std::runtime_error("Sign in to install or update this game.");
  const auto *current = installed(g.id, false);
  if (current && current->game.source != "meta")
    throw std::runtime_error("This game was added from another folder; RiftLift does not update it.");
  auto target = current ? root_of(*current) : root.empty() ? settings_.games : root.lexically_normal();
  if (!target.is_absolute())
    throw std::runtime_error("Choose an absolute folder to install to.");
  if (!std::ranges::contains(settings_.roots(), target)) {
    fs::create_directories(target);
    auto next = settings_;
    next.libraries.push_back(target);
    apply_settings(std::move(next), false);
  }
  downloads_->enqueue(g.id, g.name, target, session_->access_token, account_key_);
  // A new game takes the default for new games, unless the GUI asked.
  if (!current && settings_.add_games_to_steam && !options_.launch_requested)
    set_in_steam(g.id, settings_.steam_add_new);
  on_install_queued();
}
void Core::set_in_steam(std::string_view app_id, bool in) {
  const std::string id(app_id);
  if (in == !settings_.steam_skip.contains(id)) return;
  if (in) settings_.steam_skip.erase(id);
  else settings_.steam_skip.insert(id);
  settings_.save(data_);
  sync_steam();
}
void Core::install_for_owner(const meta::Game &g, const fs::path &root) {
  const auto game_copy = g;
  const auto owner = owner_for(game_copy.id);
  if (!owner.empty() && owner != account_key_) {
    auto other = account(owner);
    if (!other || !other->signed_in || other->needs_reauth)
      throw std::runtime_error("Reconnect the owning account before installing.");
    switch_account(owner);
  }
  install(game_copy, root);
}
void Core::request_builds(const std::string &id) {
  if (!session_ || options_.offline || !digits(id, 5) || builds_pending_.contains(id))
    return;
  builds_pending_.insert(id);
  builds_error_.erase(id);
  auto token = session_->access_token;
  auto epoch = epoch_;
  worker_->submit([this, token, epoch, id](std::stop_token stop) -> Worker::Completion {
    std::vector<meta::Build> list;
    std::string failure;
    try {
      list = meta::Store(token, stop).all_builds(id);
    } catch (...) {
      failure = exception_text();
    }
    return [this, epoch, id, list = std::move(list), failure] {
      builds_pending_.erase(id);
      if (epoch != epoch_)
        return;
      if (failure.empty())
        builds_[id] = list;
      else
        builds_error_[id] = failure;
    };
  });
}
const library::Install *Core::installed_build(std::string_view app_id, std::string_view binary_id) const {
  for (const auto &i : installs_)
    if (i.game.source == "meta" && i.game.app_id == app_id && i.game.binary_id == binary_id)
      return &i;
  return nullptr;
}
std::string Core::version_slug(const meta::Game &g, const meta::Build &b, const fs::path &root) const {
  const auto base = rift::slugify(g.package.empty() ? g.name : g.package);
  const auto jobs = downloads_->snapshot();
  // Free when no other build's record, download or folder has the name.
  auto free = [&](const std::string &slug) {
    if (!rift::valid_slug(slug))
      return false;
    if (auto i = installed_package(slug); i && i->game.binary_id != b.binary_id)
      return false;
    if (std::ranges::any_of(jobs, [&](const download::Job &j) { return j.package == slug && j.binary_id != b.binary_id; }))
      return false;
    std::error_code ec;
    auto dir = root / paths::from_utf8(slug);
    if (!fs::exists(dir, ec) || fs::is_empty(dir, ec))
      return true;
    // A paused download of this build claimed it.
    std::ifstream marker(paths::io_path(dir / ".riftlift-download"));
    std::string claimed;
    return marker && std::getline(marker, claimed) && claimed == b.binary_id;
  };
  for (auto slug : {base + "-" + rift::slugify(b.version), base + "-" + rift::slugify(b.version + " " + b.version_code),
                    base + "-" + b.binary_id})
    if (free(slug))
      return slug;
  throw std::runtime_error("No free folder name for version " + b.label() + ".");
}
size_t Core::install_versions(const meta::Game &g, const std::vector<meta::Build> &chosen, const fs::path &root) {
  if (options_.offline)
    throw std::runtime_error("Downloads are unavailable in offline mode.");
  const auto owner = owner_for(g.id);
  if (!owner.empty() && owner != account_key_) {
    auto other = account(owner);
    if (!other || !other->signed_in || other->needs_reauth)
      throw std::runtime_error("Reconnect the owning account before installing.");
    switch_account(owner);
  }
  if (!session_)
    throw std::runtime_error("Sign in to install this game.");
  auto target = root.empty() ? settings_.games : root.lexically_normal();
  if (!target.is_absolute())
    throw std::runtime_error("Choose an absolute folder to install to.");
  if (!std::ranges::contains(settings_.roots(), target)) {
    fs::create_directories(target);
    auto next = settings_;
    next.libraries.push_back(target);
    apply_settings(std::move(next), false);
  }
  size_t queued = 0;
  for (const auto &b : chosen) {
    // Versions already here stay as they are, launch settings and all.
    if (installed_build(g.id, b.binary_id))
      continue;
    downloads_->enqueue_version(g.id, b.binary_id, version_slug(g, b, target), g.name + " (" + b.version + ")", target,
                                session_->access_token, account_key_);
    ++queued;
  }
  if (queued)
    on_install_queued();
  return queued;
}
void Core::play(const meta::Game &g) { play_as(g.id, play_key(g.id)); }
std::string Core::play_key(std::string_view app_id) const {
  auto pick = owner_pick_.find(std::string(app_id));
  return pick == owner_pick_.end() ? std::string{} : pick->second;
}
const AccountView *Core::account(std::string_view key) const {
  auto it = std::ranges::find(accounts_, key, &AccountView::key);
  return it == accounts_.end() ? nullptr : &*it;
}
std::string Core::owner_for(std::string_view app_id) const {
  auto owners = game_owners(app_id);
  auto owns = [&](std::string_view key) {
    return std::ranges::find(owners, key) != owners.end();
  };
  if (auto pick = owner_pick_.find(std::string(app_id));
      pick != owner_pick_.end() && owns(pick->second))
    return pick->second;
  if (owns(account_key_))
    return account_key_;
  for (const auto &key : owners)
    if (auto a = account(key); a && a->signed_in && !a->needs_reauth)
      return key;
  return owners.empty() ? std::string{} : owners.front();
}
void Core::play_as(std::string_view id, std::string_view requested) {
  launch_game(id, requested);
}
void Core::launch_game(std::string_view id, std::string_view requested) {
  // Any installed game launches; Rift games need no account. The account
  // names whose game this is in the logs.
  auto choice = requested == "local" ? library::LaunchChoice{}
                                     : launch_choice(id, requested);
  if (choice.status == library::LaunchStatus::reconnect)
    throw std::runtime_error("Reconnect that account before playing, or choose another.");
  const std::string app_id(id);
  auto i = installed(app_id);
  if (!i)
    throw std::runtime_error(
        "This game is not completely installed. Resume its download first.");
  if ((running_.contains(app_id) && running_[app_id].process.active()) ||
      launch_pending_.contains(app_id))
    throw std::runtime_error("This game is already running or starting.");
  const auto install = *i;
  try {
    game_locks_[app_id] = std::make_unique<FileLock>(
        data_ / "locks" / ("game-" + install.plan.package + ".lock"), false);
  } catch (...) {
    throw std::runtime_error("This game is already running in another RiftLift window.");
  }
  if (settings_.runtime_override())
    log::warn("launcher", "Using the runtime set in Settings: {}",
              paths::utf8(settings_.runtime));
  bool success = false;
  action([&] {
    auto &r = running_[app_id];
    r.quitting = false;
    r.started = std::chrono::steady_clock::now();
    r.log = data_ / "logs" / ("runtime-" + install.plan.package + ".log");
    // The runtime writes its log itself; Proton's and the game's output go
    // to a separate file so lines are not written twice into one.
    r.console = data_ / "logs" / ("runtime-" + install.plan.package + ".console.log");
    // The last run's logs stay beside the new ones for debug reports.
    library::rotate_log(r.log);
    library::rotate_log(r.console);
    std::vector<std::string> args{
        "--game",       install.plan.package,
        "--log",        paths::utf8(r.log),
        "--account",    choice.account_key.empty() ? std::string("local") : choice.account_key,
        "--parent-pid", std::to_string(current_process_id())};
    if (settings_.console)
      args.push_back("--console");
    if (options_.smoke_seconds)
      args.insert(args.end(), {"--run-seconds", std::to_string(options_.smoke_seconds)});
    // RIFTLIFT_LOG_LEVEL: the launcher's log level, 0 (trace) to 4 (error).
    // RIFTLIFT_PROTON_LOG: Settings > Debug logging decides, over the older
    // app's debug-logging file.
    Environment environment{{"RIFTLIFT_LOG_LEVEL", std::to_string(settings_.log_level)},
                            {"RIFTLIFT_DATA_DIR", paths::utf8(data_)},
                            {"RIFTLIFT_PROTON_LOG", settings_.debug_logging ? "1" : "0"}};
    // The game's Platform calls (identity, entitlement) reach Meta through
    // this broker, and only when the game makes them: nothing is asked here.
    PipeChannel::Handler broker;
    if (install.game.platform_shim) {
      const auto key = choice.account_key;
      auto platform = std::make_shared<meta::PlatformBroker>(
          install.game.app_id, [data = data_, key]() -> std::optional<Session> {
            if (key.empty())
              return std::nullopt;
            return meta::AccountVault(data).load(key);
          });
      broker = [platform](const Json &request) { return platform->handle(request); };
    }
    r.process.spawn(settings_.runtime, args, r.console, settings_.console, broker, environment);
    success = true;  // the process is running, even if bookkeeping fails
    r.exit.reset();
    r.tail.clear();
    r.failure.clear();
    notice_.clear();
    try {
      r.played = std::make_unique<playtime::Session>(rift::state_dir(data_), install.plan.package);
    } catch (const std::exception &e) {
      log::warn("playtime", "Local playtime tracking could not start: {}", e.what());
    }
    if (auto entry = game(app_id)) {
      entry->recent = uint64_t(std::chrono::system_clock::to_time_t(
          std::chrono::system_clock::now()));
      save_library();
    }
    log::info("runtime", "Launched {} ({}).", install.plan.package, app_id);
  });
  if (!success) {
    game_locks_.erase(app_id);
    if (!running_[app_id].process.active()) running_.erase(app_id);
  }
}
std::string Core::add_local_game(const fs::path &executable, std::string name,
                                 std::string_view arguments, std::optional<bool> steam) {
  auto game = rift::add_game(data_, rift::local_game(executable, std::move(name), {}, arguments));
  notice_ = game.name + " is in your library.";
  rescan();
  added_to_steam(game.slug, steam);
  return game.slug;
}
void Core::added_to_steam(const std::string &slug, std::optional<bool> steam) {
  // A new game takes the Steam choice, as an install does.
  if (settings_.add_games_to_steam && !options_.launch_requested)
    set_in_steam(slug, steam.value_or(settings_.steam_add_new));
}
void Core::scan_steam_games() {
  if (steam_scanning_)
    return;
  steam_scanning_ = true;
  worker_->submit([this](std::stop_token) -> Worker::Completion {
    std::vector<rift::Game> found;
    std::string failure;
    try {
      found = rift::steam_oculus_games(steam_library::find_root());
    } catch (...) {
      failure = exception_text();
    }
    return [this, found = std::move(found), failure] {
      steam_scanning_ = false;
      steam_games_found_ = found;
      if (!failure.empty())
        error(failure);
    };
  });
}
std::string Core::add_steam_game(const rift::Game &steam, std::optional<bool> in_steam) {
  auto game = rift::add_game(data_, steam);
  notice_ = game.name + " is in your library. Steam still owns and updates it.";
  rescan();
  added_to_steam(game.slug, in_steam);
  return game.slug;
}
void Core::save_game_settings(const rift::Game &game) {
  auto current = rift::load(data_, game.slug);
  current.launch_options = game.launch_options;
  current.dll_overrides = game.dll_overrides;
  current.environment = game.environment;
  current.arguments = game.arguments;
  current.platform_shim = game.platform_shim;
  rift::save(data_, current);
  notice_ = "Game settings saved.";
  rescan();
}
playtime::Record Core::playtime_of(std::string_view app_id) const {
  auto i = installed(app_id, false);
  return i ? i->played : playtime::Record{};
}
fs::path Core::save_debug_report(std::string_view app_id, std::string_view version) const {
  DebugReport request{.data = data_, .version = std::string(version), .runtime = settings_.runtime,
                      .debug_logging = settings_.debug_logging};
  // A game no longer installed falls back to the most recent launch.
  for (const auto &install : installs_)
    if (!app_id.empty() && install.plan.app_id == app_id) {
      request.package = install.plan.package;
      request.plan = rift::record_path(data_, install.plan.package);
      request.game = install.game.name;
      if (auto g = std::ranges::find(games_, app_id, &meta::Game::id); g != games_.end()) request.game = g->name;
    }
  for (const auto &account : account_index_.accounts)
    request.private_text.insert(request.private_text.end(), {account.key, account.account_id});
  for (const auto &account : accounts_)
    request.private_text.insert(request.private_text.end(), {account.alias, account.display_name});
  return write_debug_report(request);
}
void Core::share_debug_report(fs::path file, std::function<void(std::string url, std::string error)> done) {
  worker_->submit([file = std::move(file), done = std::move(done)](std::stop_token stop) -> Worker::Completion {
    std::string url, error;
    try {
      url = upload_debug_report(file, stop);
      log::info("launcher", "Uploaded debug report {}", paths::utf8(file.filename()));
    } catch (...) {
      error = exception_text();
      log::warn("launcher", "Debug report upload failed: {}", error);
    }
    return [done, url = std::move(url), error = std::move(error)] { done(url, error); };
  });
}

void Core::sync_steam() {
  ++steam_generation_;
  if (!steam_sync_allowed_) return;
  if (scanning_) return;  // the scan in flight syncs when it finishes
  if (!steam_scan_reliable_) {
    steam_pending_ = false;
    log::warn("steam", "Steam sync skipped: installation scan is not authoritative.");
    if (steam_restart_ && !steam_library::running()) { // Steam closed for a restart; bring it back regardless.
      steam_restart_ = false;
      action([&] { steam_library::start_client(steam_library::discover_root()); });
    }
    return;
  }
  if (options_.launch_requested || steam_busy_) return;
  if (!settings_.add_games_to_steam) { steam_pending_ = false; return; }
  steam_busy_ = true;
  const auto generation = steam_generation_.load();
  std::vector<steam_library::Game> titles;
  std::set<std::string> packages;
  for (const auto& i : installs_) if (i.complete && !settings_.steam_skip.contains(i.plan.app_id) &&
                                     packages.insert(i.plan.package).second) {
    auto g = game(i.plan.app_id);
    titles.push_back({i.plan.package, g ? g->name : i.label.empty() ? i.plan.package : i.label, i.plan.app_id});
  }
  auto art = data_ / "cache/art";
  auto roots = settings_.roots();
  const bool online = !options_.offline;
  worker_->submit([this, titles, art, roots, generation, online](std::stop_token stop) -> Worker::Completion {
    try {
      // Steam's library sidebar wants the square app icon; fetch each listing's once.
      for (const auto &title : titles) {
        if (!online || !digits(title.art_id, 5) || stop.stop_requested()) continue;
        auto icon = art / (title.art_id + "_icon.image"), none = art / (title.art_id + ".noicon");
        if (fs::exists(icon) || fs::exists(none)) continue;
        try {
          auto uri = meta::Store({}, stop).artwork(title.art_id).icon;
          if (uri.empty()) paths::atomic_write(none, "");
          else paths::atomic_write(icon, http::get_image(uri, stop));
        } catch (const std::exception &e) {
          log::warn("steam", "No icon for application {}: {}", title.art_id, e.what());
        }
      }
      // A root may disappear or become unreadable after the UI's scan.
      for (const auto &root : roots) {
        if (!fs::is_directory(root)) throw std::runtime_error("Games root is unavailable.");
        (void)fs::directory_iterator(root); // Do not suppress permission errors.
      }
      auto result = steam_library::sync(steam_library::discover_root(), paths::executable(), art, titles,
          [this, generation] { return steam_generation_.load() != generation || steam_library::running(); });
      return [this, result, generation] {
        steam_busy_ = false;
        steam_pending_ = result.pending;
        steam_games_ = result.games;
        if (result.pending)
          log::info("steam", "{} games wait for Steam to close.", result.games);
        else
          log::info("steam", "Steam has {} RiftLift games.", result.games);
        if (generation != steam_generation_) { sync_steam(); return; }
        if (!steam_pending_ && steam_restart_) {
          steam_restart_ = false;
          action([&] { steam_library::start_client(steam_library::discover_root()); });
        }
      };
    } catch (const std::exception& e) {
      auto message = std::string(e.what());
      return [this, message, generation] {
        steam_busy_ = false;
        // Retrying every poll cannot fix it (an ambiguous Steam profile, an
        // unreadable root); the next scan or install tries again.
        steam_pending_ = false;
        log::warn("steam", "Steam sync skipped: {}", message);
        if (steam_restart_) {
          steam_restart_ = false;
          action([&] { steam_library::start_client(steam_library::discover_root()); });
        }
        if (generation != steam_generation_) sync_steam();
      };
    }
  });
}
void Core::restart_steam() {
  if (steam_restart_ || !steam_pending_) return;
  if (steam_library::vr_running()) return error("Close SteamVR first: Steam does not quit while SteamVR is running.");
  action([&] {
    steam_library::request_shutdown(steam_library::discover_root());
    steam_restart_ = true; // Start only after the observed close and safe sync.
  });
}
void Core::complete_sign_in(std::string callback) {
  if (!auth_ || !signing_in_ || verifying_)
    return;
  verifying_ = true;
  auth_status_ = "Verifying your Meta account...";
  auto auth = auth_;
  auto epoch = epoch_;
  worker_->submit([this, auth, callback,
                   epoch](std::stop_token stop) -> Worker::Completion {
    try {
      auto session = auth->complete(callback, stop);
      meta::Store store(session.access_token, stop);
      auto who = store.identity();
      session.account_id = who.account_id;
      session.alias = who.alias;
      session.display_name = who.display_name;
      // The account is saved even when its library cannot be listed yet;
      // Refresh lists it later.
      std::optional<meta::Snapshot> snapshot;
      std::string library_error;
      try {
        snapshot = store.library();
      } catch (...) {
        library_error = exception_text();
        log::warn("library", "Signed in, but the library could not be listed: {}", library_error);
      }
      return [this, session, snapshot, library_error, epoch] {
        if (epoch != epoch_)
          return;
        action([&] {
          if (!digits(session.account_id))
            throw std::runtime_error(
                "Meta did not return a numeric account identity. Sign-in "
                "was not saved.");
          const bool first = vault_.index().accounts.empty();
          account_key_ = vault_.upsert(session);
          session_ = session;
          account_scope_ = session.account_id;
          reload_accounts();
          downloads_->set_account(account_key_);
          downloads_->adopt_legacy(account_index_.primary);
          (void)first;
          if (snapshot) {
            library::merge(games_, *snapshot, account_scope_);
            warning_ = snapshot->warning;
            library_partial_ = !snapshot->complete;
          } else {
            refresh_error_ = library_error;
            error(library_error);
          }
          save_library();
        });
        signing_in_ = verifying_ = false;
        auth_.reset();
        auth_status_.clear();
      };
    } catch (...) {
      auto e = exception_text();
      return [this, epoch, e] {
        if (epoch == epoch_) {
          signing_in_ = verifying_ = false;
          auth_.reset();
          auth_status_.clear();
          error(e);
        }
      };
    }
  });
}
void Core::cancel_sign_in() {
  ++epoch_;
  browser_.close();
  auth_.reset();
  signing_in_ = verifying_ = false;
  auth_status_ = "Sign-in cancelled.";
}
void Core::paste_sign_in(std::string_view text) {
  if (!auth_ || !signing_in_ || verifying_)
    return;
  auto callback = meta::find_callback(text);
  if (callback.empty())
    auth_paste_error_ = "That has no oculus:// address. Copy the whole console message or the address itself.";
  else if (!auth_->accepts(callback))
    auth_paste_error_ = "That address is from an earlier sign-in. Copy the one Meta gave for this sign-in.";
  else {
    auth_paste_error_.clear();
    complete_sign_in(std::move(callback));
  }
}
void Core::tick() {
  worker_->drain();
  if ((steam_pending_ || steam_restart_) && !steam_busy_ && now_ms() - steam_poll_ > 2000) {
    steam_poll_ = now_ms();
    if (!steam_library::running()) {
      if (!settings_.add_games_to_steam && steam_restart_) {
        steam_restart_ = false;
        action([&] { steam_library::start_client(steam_library::discover_root()); });
      } else sync_steam();
    }
  }
  while (auto event = browser_.poll()) {
    if (event->kind == AuthBrowser::Kind::ready) {
      auth_page_loaded_ = true;
      auth_status_ = "Meta sign-in is ready.";
      log::info("auth", "Meta sign-in page loaded.");
    }
    if (event->kind == AuthBrowser::Kind::error) {
      log::warn("auth", "Meta's sign-in window could not open: {}", event->value);
      if (signing_in_ && auth_ && !verifying_) {
        // The sign-in still works in any browser: keep waiting for its address.
        auth_manual_ = true;
        auth_status_ = "Meta's sign-in window could not open. Sign in manually below.";
      } else {
        signing_in_ = false;
        auth_status_.clear();
        error(event->value);
      }
    }
    if (event->kind == AuthBrowser::Kind::closed && signing_in_ &&
        !verifying_) {
      ++epoch_;
      signing_in_ = false;
      auth_.reset();
      auth_status_ = "Sign-in cancelled.";
    }
    if (event->kind == AuthBrowser::Kind::callback)
      complete_sign_in(std::move(event->value));
  }
  // Only for a window: the command line keeps asking for the address.
  if (!options_.cli && !options_.automation) {
    const bool wanted = signing_in_ && auth_manual_;
    if (wanted && link_handler_ == 0) {
      link_handler_ = -1;
      try {
        if (sign_in_link::take_over(data_)) {
          link_handler_ = 1;
          log::info("auth", "RiftLift opens oculus:// links until this sign-in ends.");
        }
      } catch (const std::exception &e) {
        log::warn("auth", "RiftLift could not open oculus:// links: {}", e.what());
      }
    } else if (!wanted && link_handler_ != 0) {
      if (link_handler_ == 1)
        try {
          sign_in_link::restore(data_);
        } catch (const std::exception &e) {
          log::warn("auth", "The previous oculus:// handler could not be put back: {}", e.what());
        }
      link_handler_ = 0;
      auth_paste_open_ = false;
    }
  }
  auto now = now_ms();
  if (now - last_poll_ > 400) {
    last_poll_ = now;
    if (link_handler_ == 1 && !verifying_)
      if (auto address = sign_in_link::receive(data_); !address.empty()) {
        log::info("auth", "The browser handed back Meta's sign-in address.");
        paste_sign_in(address);
      }
    bool exited = false;
    for (auto &[id, r] : running_) {
      auto previous = r.exit;
      r.exit = r.process.poll();
      r.tail = tail_log(r.log);
      if (r.played && !r.exit)
        r.played->tick();
      if (r.exit && !previous) {
        game_locks_.erase(id);
        if (r.played)
          try {
            r.played->close();
          } catch (const std::exception &e) {
            log::warn("playtime", "Local playtime could not be saved: {}", e.what());
          }
        log::info("runtime", "Rift application {} exited with code {}.", id,
                  *r.exit);
        exited = true;
        int entitlement = r.quitting ? 0 : library::entitlement_error(r.log);
        // A crash right after Meta refused another platform request as signed out.
        if (!entitlement && !r.quitting && *r.exit != 0 && library::crashed_signed_out(r.log))
          entitlement = int(meta::PlatformError::unauthenticated);
        if (entitlement == int(meta::PlatformError::unauthenticated) ||
            entitlement == int(meta::PlatformError::not_entitled)) {
          // The game quit after Meta refused its ownership check.
          auto entry = game(id);
          r.failure = entitlement == int(meta::PlatformError::unauthenticated)
                          ? i18n::tr("It needs a Meta account. Sign in with the account that owns it, then start "
                                     "it again.")
                          : i18n::tr("The Meta account it started with does not own it. Choose the account that "
                                     "owns it, then start it again.");
          error(i18n::trf("{} closed: {}", entry ? entry->name : id, r.failure));
        } else if (*r.exit != 0 && !r.quitting) {
          // A runtime that failed before opening its log still wrote stderr.
          r.failure = library::runtime_failure(
              r.tail.empty() ? tail_log(r.console) : r.tail, *r.exit);
          // A game that crashed: a known cause in its logs says why.
          if (r.failure.starts_with("The game stopped unexpectedly"))
            if (auto d = diagnose(tail_log(r.log, 512 << 10) + "\n" + tail_log(r.console, 512 << 10)))
              r.failure = std::format("{} {}", i18n::tr(d->cause), i18n::tr(d->advice));
          auto entry = game(id);
          error(i18n::trf("{} stopped: {}", entry ? entry->name : id,
                            r.failure));
        }
      }
    }
    // Fresh playtime and "Recently played" order once a game has closed.
    if (exited)
      rescan();
    auto jobs = downloads_->snapshot();
    auto complete = std::ranges::count_if(
        jobs, [](auto &j) { return j.state == download::State::complete; });
    const auto discarded = downloads_->discarded();
    if (size_t(complete) != last_completed_ || discarded != last_discarded_) {
      last_completed_ = complete;
      last_discarded_ = discarded;
      rescan();
    }
  }
}
} // namespace riftlift::launcher
