// riftlift-cli: the RiftLift launcher from a terminal. Every command drives
// launcher::Core, the same implementation the launcher window uses, so an
// install or launch here is exactly the one the window performs.
#include "common/log.h"
#include "common/search.h"
#include "launcher/core.h"
#include "launcher/ui/rich_text.h"

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <cstdio>
#include <deque>
#include <fstream>
#include <map>
#include <mutex>
#include <set>
#include <format>
#include <iostream>

#ifdef _WIN32
#include <io.h>
#include <windows.h>
#else
#include <unistd.h>
#endif

namespace riftlift::launcher {
namespace {
volatile std::sig_atomic_t interrupted = 0;
void on_interrupt(int) { interrupted = 1; }

constexpr const char *kUsage = R"(RiftLift command line

Usage: riftlift-cli [--json] [--offline] <command> [arguments]

Library
  status                         Accounts, games folder, installs and downloads
  library [--installed] [--search TEXT]
                                 Games your accounts own and games added here
  info GAME                      Store details, installation, launch settings
                                 and playtime
  install GAME [--as ACCOUNT] [--to FOLDER]
                                 Download and install (or update) a Rift game;
                                 a new one goes to FOLDER instead of the games
                                 folder
  uninstall GAME [--yes]         Remove a game from RiftLift; a store install's
                                 files go too, never a local or Steam game's
  play GAME [--as ACCOUNT|local] Launch a game and wait for it to exit
  logs GAME [--lines N]          The end of a game's last runtime log
  report [GAME] [--upload]       Write a debug report about a game's last run
                                 (the most recent one by default): its likely
                                 cause, component checks, system setup and
                                 logs, with personal details removed; --upload
                                 also shares it as a link (dpaste.com, 90 days)

Adding games
  add EXE [--name NAME] [--args "ARGS"]
                                 Add a Rift game installed elsewhere on this PC
  add --steam [GAME|--all]       List installed Steam games with an Oculus mode,
                                 or add one (or all) of them
  add LINK                       Add a game Meta does not list in your PC
                                 library (a cross-buy title) by its store link

Launch settings
  configure GAME [--options "ARGS"] [--dll-overrides RULES]
                 [--env NAME=VALUE]... [--clear-env] [--shim on|off]
                                 Extra launch options, Wine DLL overrides
                                 (version=n,b;...), environment variables and
                                 whether RiftLift's platform shim is used

Downloads
  downloads                      The download queue
  downloads cancel GAME          Cancel a game's download
  downloads resume GAME          Resume a paused or failed download

Accounts
  accounts                       Signed-in Meta accounts
  accounts sign-in               Add an account (opens Meta's sign-in window)
  accounts use ACCOUNT           Make an account active
  accounts primary ACCOUNT       Mark the primary account
  accounts sign-out ACCOUNT      Sign out; its games stay
  accounts remove ACCOUNT [--yes]

Settings
  settings                       Show settings
  settings set KEY VALUE         games-folder PATH | runtime PATH|default |
                                 log-level trace|debug|info|warn|error |
                                 console on|off | debug-logging on|off |
                                 add-to-steam on|off |
                                 add-new-to-steam on|off | language auto|en|fr

GAME is a name (or a unique part of it), an app id or a slug.
ACCOUNT is a name, alias, account key or the number `accounts` shows.
Exit codes: 0 success, 1 failure, 2 usage, 130 interrupted; play returns the
game's own exit code.
)";

struct UsageError : std::runtime_error {
  using std::runtime_error::runtime_error;
};
struct Interrupted {};

struct Args {
  std::vector<std::string> words;
  std::map<std::string, std::string> values;
  std::vector<std::string> env;  // each --env NAME=VALUE, in order
  std::set<std::string> flags;
  bool flag(std::string_view f) const { return flags.contains(std::string(f)); }
  std::optional<std::string> value(std::string_view v) const {
    auto it = values.find(std::string(v));
    return it == values.end() ? std::nullopt : std::optional(it->second);
  }
  const std::string &word(size_t i, std::string_view what) const {
    if (i >= words.size())
      throw UsageError(std::format("Missing {}.", what));
    return words[i];
  }
};

Args parse(const std::vector<std::string> &argv) {
  static const std::set<std::string> with_value{"--as",      "--to",      "--search",        "--lines", "--name",
                                                "--args",    "--options", "--dll-overrides", "--shim"};
  static const std::set<std::string> known{"--json", "--offline",   "--yes",   "-y",        "--all",    "--installed",
                                           "--steam", "--clear-env", "--help", "-h",        "--version", "--upload"};
  Args out;
  for (size_t i = 1; i < argv.size(); ++i) {
    const auto &a = argv[i];
    if (a == "--env") {
      if (i + 1 >= argv.size())
        throw UsageError("--env needs NAME=VALUE.");
      out.env.push_back(argv[++i]);
    } else if (with_value.contains(a)) {
      if (i + 1 >= argv.size())
        throw UsageError(std::format("{} needs a value.", a));
      out.values[a] = argv[++i];
    } else if (known.contains(a)) {
      out.flags.insert(a == "-y" ? "--yes" : a == "-h" ? "--help" : a);
    } else if (a.starts_with("--")) {
      throw UsageError(std::format("Unknown option {}.", a));
    } else {
      out.words.push_back(a);
    }
  }
  return out;
}

bool terminal(FILE *f) {
#ifdef _WIN32
  // _isatty is also true for NUL and other character devices.
  DWORD mode;
  return GetConsoleMode(reinterpret_cast<HANDLE>(_get_osfhandle(_fileno(f))), &mode) != 0;
#else
  return isatty(fileno(f)) != 0;
#endif
}

std::string lower(std::string s) {
  for (auto &c : s)
    c = char(std::tolower(static_cast<unsigned char>(c)));
  return s;
}

std::string bytes(uint64_t n) {
  const char *units[] = {"B", "KB", "MB", "GB", "TB"};
  double v = double(n);
  int u = 0;
  while (v >= 1000 && u < 4) {
    v /= 1000;
    ++u;
  }
  return u == 0 ? std::format("{} B", n) : std::format("{:.1f} {}", v, units[u]);
}

std::string duration(double seconds) {
  auto s = uint64_t(std::max(0.0, seconds));
  if (s >= 3600)
    return std::format("{}h{:02}m", s / 3600, s / 60 % 60);
  if (s >= 60)
    return std::format("{}m{:02}s", s / 60, s % 60);
  return std::format("{}s", s);
}

// Columns padded to their widest cell (UTF-8 aware enough for names).
void table(const std::vector<std::string> &head, const std::vector<std::vector<std::string>> &rows) {
  auto width = [](const std::string &s) {
    return size_t(std::ranges::count_if(s, [](char c) { return (static_cast<unsigned char>(c) & 0xC0) != 0x80; }));
  };
  std::vector<size_t> w(head.size());
  for (size_t c = 0; c < head.size(); ++c) {
    w[c] = width(head[c]);
    for (auto &r : rows)
      w[c] = std::max(w[c], width(r[c]));
  }
  auto line = [&](const std::vector<std::string> &r) {
    std::string out;
    for (size_t c = 0; c < r.size(); ++c) {
      out += r[c];
      if (c + 1 < r.size())
        out += std::string(w[c] - width(r[c]) + 2, ' ');
    }
    std::cout << out << "\n";
  };
  line(head);
  for (auto &r : rows)
    line(r);
}

bool confirm(const Args &args, const std::string &question) {
  if (args.flag("--yes"))
    return true;
  if (!terminal(stdin))
    throw UsageError(question + " Pass --yes to confirm.");
  std::cout << question << " [y/N] " << std::flush;
  std::string answer;
  std::getline(std::cin, answer);
  if (lower(answer) == "y" || lower(answer) == "yes")
    return true;
  std::cout << "Cancelled.\n";
  return false;
}

const char *job_state(download::State s) { return download::label(s); }

// A store description (Markdown or HTML) as wrapped plain text, cut after
// about `limit` characters.
std::string plain_description(std::string_view text, size_t limit) {
  std::string out;
  for (const auto &block : ui::parse_description(text)) {
    std::string words;
    for (const auto &span : block.spans)
      words += span.text;
    if (block.kind == ui::Block::Kind::rule || words.find_first_not_of(" \t") == std::string::npos)
      continue;
    std::string prefix, indent;
    if (block.kind == ui::Block::Kind::bullet) {
      prefix = "  - ";
      indent = "    ";
    } else if (block.kind == ui::Block::Kind::numbered) {
      prefix = "  " + block.marker + " ";
      indent = std::string(prefix.size(), ' ');
    }
    if (!out.empty() && (block.kind == ui::Block::Kind::heading || block.kind == ui::Block::Kind::paragraph ||
                         block.after_blank))
      out += "\n";
    std::string line = prefix;
    size_t column = 0;
    for (size_t at = 0; at < words.size();) {
      auto end = words.find(' ', at);
      auto word = words.substr(at, end == std::string::npos ? std::string::npos : end - at);
      at = end == std::string::npos ? words.size() : end + 1;
      if (word.empty())
        continue;
      if (column && column + 1 + word.size() + prefix.size() > 78) {
        out += line + "\n";
        line = indent;
        column = 0;
      }
      line += (column ? " " : "") + word;
      column += word.size() + (column ? 1 : 0);
    }
    out += line + "\n";
    if (out.size() > limit) {
      out += "...\n";
      break;
    }
  }
  return out;
}

class Cli : public Core {
public:
  Cli(Options options, bool json) : Core(std::move(options)), json_(json) {}
  ~Cli() override { shutdown(); }
  int run(const Args &args);

private:
  bool json_;
  std::mutex mutex_;
  std::condition_variable cv_;
  bool woken_ = false;
  void wake() override {
    {
      std::lock_guard lock(mutex_);
      woken_ = true;
    }
    cv_.notify_all();
  }
  // Applies completions until `done`; throws Interrupted on Ctrl+C.
  void wait(const std::function<bool()> &done, const std::function<void()> &each = {}) {
    for (;;) {
      tick();
      if (done())
        return;
      if (interrupted)
        throw Interrupted{};
      if (each)
        each();
      std::unique_lock lock(mutex_);
      cv_.wait_for(lock, std::chrono::milliseconds(200), [&] { return woken_; });
      woken_ = false;
    }
  }
  void wait_scan() { wait([&] { return !scanning_; }); }
  // The Meta library of every signed-in account, or the cached one offline.
  void wait_library() {
    wait([&] { return !refreshing_ && !scanning_ && library_pending_.empty(); });
    if (!refresh_error_.empty())
      std::cerr << "warning: could not refresh the Meta library (" << refresh_error_
                << "); showing the saved library.\n";
  }
  void wait_details(const std::string &id) {
    if (options_.offline || !session_)
      return;
    select(id);
    wait([&] { return !details_pending_.contains(id); });
  }
  // --as ACCOUNT: the owning account this command acts as.
  void pick_owner(const Args &args, const meta::Game &g) {
    auto as = args.value("--as");
    if (!as)
      return;
    auto key = resolve_account(*as);
    auto owners = game_owners(g.id);
    if (std::ranges::find(owners, key) == owners.end())
      throw std::runtime_error(std::format("{} does not own {}.", account_label(key), g.name));
    owner_pick_[g.id] = key;
  }
  // Runs `fn` as the account that owns the game (as the window's Install
  // switches to it), then returns to the account that was active.
  template <class F> int as_owner(const meta::Game &g, F &&fn) {
    const auto home = account_key_;
    auto restore = [&] {
      if (!home.empty() && account_key_ != home)
        action([&] { switch_account(home); });
    };
    try {
      const auto owner = owner_for(g.id);
      if (!owner.empty() && owner != account_key_) {
        auto other = account(owner);
        if (!other || !other->signed_in || other->needs_reauth)
          throw std::runtime_error(std::format("Reconnect {}, which owns {}.", account_label(owner), g.name));
        switch_account(owner);
        wait_library();
      }
      int rc = fn();
      restore();
      return rc;
    } catch (...) {
      restore();
      throw;
    }
  }
  void print(const Json &j) { std::cout << j.dump(2) << "\n"; }
  void need_queue() {
    if (!owns_download_queue())
      throw std::runtime_error("The RiftLift launcher is open and manages downloads. Use it, or close it first.");
  }
  void need_online() {
    if (options_.offline)
      throw std::runtime_error("This needs a connection. Run without --offline.");
    if (!session_)
      throw std::runtime_error("Sign in first: riftlift-cli accounts sign-in");
  }

  std::string account_name(const AccountView &a) const {
    return !a.display_name.empty() ? a.display_name : !a.alias.empty() ? a.alias : "Meta account";
  }
  std::string account_label(std::string_view key) const {
    if (key == "local")
      return "no account";
    auto a = account(key);
    return a ? account_name(*a) : std::string(key);
  }
  std::string resolve_account(const std::string &query, bool allow_local = false) const {
    if (allow_local && query == "local")
      return "local";
    const auto q = lower(query);
    std::vector<std::string> found;
    for (size_t i = 0; i < accounts_.size(); ++i) {
      const auto &a = accounts_[i];
      if (a.key == query || std::to_string(i + 1) == query || (!a.alias.empty() && lower(a.alias) == q) ||
          (!a.display_name.empty() && lower(a.display_name) == q))
        found.push_back(a.key);
    }
    if (found.size() == 1)
      return found.front();
    throw std::runtime_error(found.empty() ? std::format("No account matches \"{}\". See: riftlift-cli accounts", query)
                                           : std::format("\"{}\" matches several accounts; use its key.", query));
  }

  std::string package_of(const meta::Game &g) const {
    if (auto i = installed(g.id, false))
      return i->plan.package;
    return g.package;
  }
  meta::Game resolve_game(const std::string &query) const {
    auto games = visible_games();
    const auto q = lower(query);
    auto pick = [&](auto pred) {
      std::vector<meta::Game> out;
      for (auto &g : games)
        if (pred(g))
          out.push_back(g);
      return out;
    };
    for (auto found : {pick([&](auto &g) { return g.id == query; }),
                       pick([&](auto &g) { return !package_of(g).empty() && package_of(g) == query; }),
                       pick([&](auto &g) { return lower(g.name) == q; }),
                       pick([&](auto &g) { return lower(g.name).find(q) != std::string::npos; })}) {
      if (found.size() == 1)
        return found.front();
      if (found.size() > 1) {
        std::string names;
        for (size_t i = 0; i < found.size() && i < 8; ++i)
          names += std::format("\n  {} ({})", found[i].name, found[i].id);
        throw std::runtime_error(std::format("\"{}\" matches {} games:{}", query, found.size(), names));
      }
    }
    throw std::runtime_error(std::format("No game matches \"{}\". See: riftlift-cli library", query));
  }

  struct GameState {
    std::string label;
    const library::Install *install = nullptr;
    std::optional<download::Job> job;
  };
  GameState state_of(const meta::Game &g) const {
    GameState s;
    s.install = installed(g.id, false);
    s.job = download::base_job(downloads_->snapshot(), g.id);
    bool active = s.job && (s.job->state == download::State::downloading || s.job->state == download::State::queued ||
                            s.job->state == download::State::checking);
    if (active)
      s.label = "downloading";
    else if (s.job && s.job->state == download::State::failed)
      s.label = "needs attention";
    else if (s.install && s.install->complete)
      s.label = "installed";
    else if (s.install && s.install->plan.local)
      s.label = "files missing";
    else if (s.install || (s.job && s.job->state == download::State::paused && s.job->done))
      s.label = "paused";
    else
      s.label = "not installed";
    return s;
  }
  Json game_json(const meta::Game &g) const {
    auto s = state_of(g);
    Json j{{"id", g.id}, {"name", g.name}, {"publisher", g.publisher}, {"status", s.label}};
    auto package = package_of(g);
    if (!package.empty())
      j["package"] = package;
    Json owners = Json::array();
    for (auto &key : game_owners(g.id))
      owners.push_back(account_label(key));
    j["owners"] = owners;
    if (s.install)
      j["install"] = {{"directory", paths::utf8(s.install->directory)},
                      {"version", s.install->plan.version},
                      {"versionCode", s.install->plan.version_code},
                      {"complete", s.install->complete},
                      {"bytes", s.install->bytes},
                      {"source", s.install->game.source},
                      {"slug", s.install->game.slug},
                      {"executable", s.install->game.executable},
                      {"launchOptions", s.install->game.launch_options},
                      {"dllOverrides", s.install->game.dll_overrides},
                      {"environment", s.install->game.environment}};
    auto played = playtime_of(g.id);
    j["playtime"] = {{"seconds", played.seconds}, {"launches", played.launches}};
    if (s.job)
      j["download"] = {{"state", job_state(s.job->state)}, {"done", s.job->done}, {"total", s.job->total},
                       {"error", s.job->error}};
    return j;
  }

  // Follows one queued job to its end, printing progress.
  download::Job follow(const std::string &id, const std::string &verb) {
    const bool tty = terminal(stdout) && !json_;
    std::optional<download::Job> last;
    auto started = std::chrono::steady_clock::now();
    auto printed = started - std::chrono::hours(1);
    int printed_tenth = -1;
    // Downloads queue under each game's owner, which need not be the active account.
    auto find = [&]() -> std::optional<download::Job> {
      auto jobs = downloads_->snapshot();
      for (auto &a : accounts_)
        if (a.key != account_key_)
          std::ranges::move(downloads_->snapshot(a.key), std::back_inserter(jobs));
      for (auto &j : jobs)
        if (j.app_id == id)
          return j;
      return std::nullopt;
    };
    wait(
        [&] {
          last = find();
          return !last || last->state == download::State::complete || last->state == download::State::failed ||
                 last->state == download::State::paused;
        },
        [&] {
          if (!last || json_)
            return;
          auto &j = *last;
          auto now = std::chrono::steady_clock::now();
          std::string text = std::format("{} {}", verb, j.name.empty() ? id : j.name);
          if (j.state == download::State::queued)
            text += "  queued";
          else if (j.state == download::State::checking)
            text += "  checking files";
          else if (j.total) {
            const double f = double(j.done) / double(j.total);
            text += std::format("  {:>3}%  {} of {}", int(f * 100), bytes(j.done), bytes(j.total));
            if (j.bytes_per_second > 1)
              text += std::format("  {}/s  {} left", bytes(uint64_t(j.bytes_per_second)),
                                  duration(double(j.total - j.done) / j.bytes_per_second));
          }
          if (tty) {
            if (now - printed > std::chrono::milliseconds(250)) {
              std::cout << "\r" << text << "\x1b[K" << std::flush;
              printed = now;
            }
          } else {
            int tenth = j.total ? int(10 * j.done / j.total) : -1;
            if (tenth != printed_tenth || now - printed > std::chrono::seconds(60)) {
              std::cout << text << "\n" << std::flush;
              printed_tenth = tenth;
              printed = now;
            }
          }
        });
    if (tty)
      std::cout << "\r\x1b[K" << std::flush;
    if (!last)
      throw std::runtime_error("The job left the queue before it finished.");
    if (last->state != download::State::complete)
      throw std::runtime_error(last->error.empty() ? std::format("The {} did not finish.", lower(verb))
                                                   : last->error);
    return *last;
  }

  int status(const Args &);
  int library_list(const Args &);
  int info(const Args &);
  int install_game(const Args &);
  int uninstall_game(const Args &);
  int play_game(const Args &);
  int logs(const Args &);
  int report(const Args &);
  int add_cmd(const Args &);
  int configure_cmd(const Args &);
  int downloads_cmd(const Args &);
  int accounts_cmd(const Args &);
  int settings_cmd(const Args &);
};

int Cli::status(const Args &) {
  wait_scan();
  Json accounts = Json::array();
  for (auto &a : accounts_)
    accounts.push_back({{"name", account_name(a)}, {"active", a.active}, {"primary", a.primary},
                        {"signedIn", a.signed_in && !a.needs_reauth}});
  size_t complete = std::ranges::count_if(installs_, [](auto &i) { return i.complete; });
  uint64_t total = 0;
  for (auto &i : installs_)
    total += i.bytes;
  Json jobs = Json::array();
  for (auto &j : downloads_->snapshot())
    if (j.state != download::State::complete)
      jobs.push_back({{"name", j.name}, {"state", job_state(j.state)}});
  Json out{{"version", RIFTLIFT_VERSION},
           {"dataFolder", paths::utf8(data_)},
           {"gamesFolder", paths::utf8(settings_.games)},
           {"accounts", accounts},
           {"installed", complete},
           {"installedBytes", total},
           {"downloads", jobs},
           {"launcherOpen", !owns_download_queue()}};
  if (json_)
    return print(out), 0;
  std::cout << "RiftLift " << RIFTLIFT_VERSION << "\n";
  std::cout << "Games folder: " << paths::utf8(settings_.games) << "\n";
  if (accounts_.empty())
    std::cout << "Accounts:     none (riftlift-cli accounts sign-in)\n";
  for (auto &a : accounts_)
    if (a.active)
      std::cout << "Account:      " << account_name(a) << " (" << accounts_.size() << " signed in)\n";
  std::cout << "Installed:    " << complete << " games, " << bytes(total) << "\n";
  std::cout << "Downloads:    " << (jobs.empty() ? std::string("none") : std::to_string(jobs.size()) + " in the queue")
            << "\n";
  if (!owns_download_queue())
    std::cout << "The RiftLift launcher is open; it manages downloads until it closes.\n";
  return 0;
}

int Cli::library_list(const Args &args) {
  wait_library();
  auto games = visible_games();
  const bool only_installed = args.flag("--installed");
  const auto query = args.value("--search").value_or("");
  std::erase_if(games, [&](auto &g) {
    return (only_installed && !installed(g.id, false)) || !search::matches(query, g.name);
  });
  std::ranges::sort(games, [](auto &a, auto &b) { return lower(a.name) < lower(b.name); });
  if (json_) {
    Json out = Json::array();
    for (auto &g : games)
      out.push_back(game_json(g));
    return print(out), 0;
  }
  if (games.empty()) {
    std::cout << (only_installed || !query.empty() ? "No games match.\n" : "No games yet.\n");
    return 0;
  }
  std::vector<std::vector<std::string>> rows;
  for (auto &g : games) {
    auto s = state_of(g);
    rows.push_back({g.name, s.label, s.install ? s.install->plan.version : "",
                    s.install ? bytes(s.install->bytes) : "", g.id});
  }
  table({"Game", "Status", "Version", "Size", "App ID"}, rows);
  std::cout << std::format("\n{} games, {} installed\n", games.size(),
                           std::ranges::count_if(games, [&](auto &g) { return installed(g.id) != nullptr; }));
  return 0;
}

int Cli::info(const Args &args) {
  wait_library();
  auto g = resolve_game(args.word(1, "game"));
  wait_details(g.id);
  if (auto found = game(g.id))
    g = *found;
  auto j = game_json(g);
  auto detail = details_.find(g.id);
  if (detail != details_.end()) {
    j["latestVersion"] = detail->second.version;
    j["downloadBytes"] = detail->second.size;
  }
  j["updateAvailable"] = update_available(g.id);
  j["description"] = g.description;
  if (json_)
    return print(j), 0;
  auto s = state_of(g);
  std::cout << g.name << "\n";
  if (!g.publisher.empty())
    std::cout << "Publisher:  " << g.publisher << "\n";
  std::cout << "App ID:     " << g.id << "\n";
  if (auto p = package_of(g); !p.empty())
    std::cout << "Package:    " << p << "\n";
  std::string owners;
  for (auto &key : game_owners(g.id))
    owners += (owners.empty() ? "" : ", ") + account_label(key);
  std::cout << "Owned by:   " << (owners.empty() ? "no signed-in account" : owners) << "\n";
  std::cout << "Status:     " << s.label << (update_available(g.id) ? " (update available)" : "") << "\n";
  if (s.install) {
    std::cout << "Installed:  version " << s.install->plan.version << ", " << bytes(s.install->bytes) << "\n";
    std::cout << "Folder:     " << paths::utf8(s.install->directory) << "\n";
  }
  if (detail != details_.end() && !detail->second.version.empty())
    std::cout << "Latest:     version " << detail->second.version
              << (detail->second.size ? ", " + bytes(detail->second.size) : "") << "\n";
  if (s.install) {
    const auto &r = s.install->game;
    std::cout << "Source:     " << (r.source == "steam" ? "Steam" : r.source == "local" ? "added from this PC" : "Meta store")
              << "\n";
    std::cout << "Executable: " << r.executable << (r.arguments.empty() ? "" : " " + rift::join_arguments(r.arguments))
              << "\n";
    if (!r.launch_options.empty())
      std::cout << "Options:    " << rift::join_arguments(r.launch_options) << "\n";
    if (!r.dll_overrides.empty())
      std::cout << "DLLs:       " << r.dll_overrides << "\n";
    for (auto &[k, v] : r.environment)
      std::cout << "Env:        " << k << "=" << v << "\n";
    auto played = playtime_of(g.id);
    std::cout << "Played:     " << (played.launches ? std::format("{} over {} launch{}", playtime::format(played.seconds),
                                                                     played.launches, played.launches == 1 ? "" : "es")
                                                    : std::string("never"))
              << "\n";
  }
  if (auto d = plain_description(g.description, 700); !d.empty())
    std::cout << "\n" << d;
  if (auto e = detail_errors_.find(g.id); e != detail_errors_.end())
    std::cerr << "warning: store details unavailable: " << e->second << "\n";
  return 0;
}

int Cli::install_game(const Args &args) {
  need_online();
  need_queue();
  wait_library();
  auto g = resolve_game(args.word(1, "game"));
  if (game_owners(g.id).empty())
    throw std::runtime_error(std::format("No signed-in account owns {}.", g.name));
  pick_owner(args, g);
  if (installed(g.id)) {
    wait_details(g.id);
    if (!update_available(g.id)) {
      if (json_)
        return print(game_json(g)), 0;
      std::cout << g.name << " is installed and up to date.\n";
      return 0;
    }
  }
  const auto home = account_key_;
  auto restore = [&] {
    // Finding the owner switches accounts as the window does; the command
    // leaves the active account as it found it.
    if (!home.empty() && account_key_ != home)
      action([&] { switch_account(home); });
  };
  try {
    auto entry = game(g.id);
    if (!entry)
      throw std::runtime_error(std::format("{} is not in the Meta library.", g.name));
    const auto to = args.value("--to");
    install_for_owner(*entry, to ? fs::absolute(paths::from_utf8(*to)).lexically_normal() : fs::path{});
    if (!json_)
      std::cout << "Installing " << g.name << " as " << account_label(account_key_) << "\n";
    follow(g.id, "Downloading");
    // Judge the files from a scan that started after the job completed.
    wait_scan();
    rescan();
    wait_scan();
  } catch (...) {
    restore();
    throw;
  }
  restore();
  auto done = installed(g.id);
  if (!done)
    throw std::runtime_error("The installed files did not pass the size check.");
  if (json_)
    return print(game_json(g)), 0;
  std::cout << std::format("Installed {} (version {}, {}).\n", g.name, done->plan.version, bytes(done->bytes));
  return 0;
}

int Cli::uninstall_game(const Args &args) {
  wait_scan();
  auto g = resolve_game(args.word(1, "game"));
  auto i = installed(g.id, false);
  if (!i)
    throw std::runtime_error(std::format("{} is not installed.", g.name));
  const bool files = i->game.source == "meta";
  if (!confirm(args, files ? std::format("Uninstall {} and delete its files ({})?", g.name, bytes(i->bytes))
                           : std::format("Remove {} from RiftLift? Its files stay where they are.", g.name)))
    return 1;
  const auto copy = *i;
  uninstall(copy);
  wait_scan();
  if (json_)
    return print({{"id", g.id}, {"name", g.name}, {"uninstalled", true}}), 0;
  std::cout << (files ? "Uninstalled " : "Removed ") << g.name << ".\n";
  return 0;
}

int Cli::play_game(const Args &args) {
  wait_scan();
  auto g = resolve_game(args.word(1, "game"));
  if (!installed(g.id))
    throw std::runtime_error(std::format("{} is not installed. Install it first: riftlift-cli install \"{}\"", g.name,
                                         g.name));
  std::string key = play_key(g.id);
  if (auto as = args.value("--as"))
    key = resolve_account(*as, true);
  launch_game(g.id, key);
  if (!json_)
    std::cout << "Starting " << g.name << "\n" << std::flush;
  wait([&] { return !launch_pending_.contains(g.id); });
  auto r = running_.find(g.id);
  if (r == running_.end())
    throw std::runtime_error(notice_.empty() ? "The game did not start." : notice_);
  if (!json_)
    std::cout << "Running. Log: " << paths::utf8(r->second.log) << "\n" << std::flush;
  bool stopped = false;
  try {
    wait([&] { return r->second.exit.has_value(); });
  } catch (const Interrupted &) {
    // The game quits through the Activity lifecycle, as it would when this
    // process exits. A second Ctrl+C leaves now.
    std::cerr << "\nStopping " << g.name << "...\n";
    interrupted = 0;
    r->second.process.request_stop();
    const auto until = std::chrono::steady_clock::now() + std::chrono::seconds(30);
    wait([&] { return r->second.exit.has_value() || std::chrono::steady_clock::now() > until; });
    if (!r->second.exit)
      throw;
    stopped = true;
  }
  const int code = *r->second.exit;
  if (json_)
    print({{"id", g.id}, {"name", g.name}, {"exitCode", code}, {"stopped", stopped}, {"error", r->second.failure},
           {"log", paths::utf8(r->second.log)}});
  else if (code != 0)
    std::cerr << g.name << " stopped: " << r->second.failure << "\n";
  else
    std::cout << g.name << (stopped ? " stopped.\n" : " exited.\n");
  return stopped && code == 0 ? 130 : code;
}

int Cli::logs(const Args &args) {
  wait_scan();
  auto g = resolve_game(args.word(1, "game"));
  auto package = package_of(g);
  if (package.empty())
    throw std::runtime_error(std::format("{} has no installation to log.", g.name));
  auto file = data_ / "logs" / ("runtime-" + package + ".log");
  if (!fs::exists(file))
    throw std::runtime_error(std::format("{} has not run yet.", g.name));
  const auto wanted = size_t(number(Json(args.value("--lines").value_or("40"))));
  std::ifstream in(file, std::ios::binary);
  std::deque<std::string> lines;
  for (std::string line; std::getline(in, line);) {
    if (!line.empty() && line.back() == '\r')
      line.pop_back();
    lines.push_back(std::move(line));
    if (lines.size() > wanted)
      lines.pop_front();
  }
  if (json_)
    return print({{"log", paths::utf8(file)}, {"lines", lines}}), 0;
  std::cout << "# " << paths::utf8(file) << "\n";
  for (auto &l : lines)
    std::cout << l << "\n";
  return 0;
}

int Cli::report(const Args &args) {
  wait_scan();
  std::string app_id;
  if (args.words.size() > 1)
    app_id = resolve_game(args.word(1, "game")).id;
  const auto file = save_debug_report(app_id, RIFTLIFT_VERSION);
  std::string url, error;
  if (args.flag("--upload")) {
    bool done = false;
    share_debug_report(file, [&](std::string u, std::string e) {
      url = std::move(u);
      error = std::move(e);
      done = true;
    });
    wait([&] { return done; });
  }
  if (json_) {
    Json out{{"report", paths::utf8(file)}};
    if (!url.empty()) out["url"] = url;
    if (!error.empty()) out["error"] = error;
    print(out);
  } else {
    // The summary: everything before the checks.
    std::ifstream in(file, std::ios::binary);
    for (std::string line; std::getline(in, line) && line != "=== Checks ===";)
      std::cout << line << "\n";
    std::cout << "Full report: " << paths::utf8(file) << "\n";
    if (!url.empty()) std::cout << "Link to share: " << url << "\n";
  }
  if (!error.empty())
    throw std::runtime_error("The report could not be uploaded: " + error);
  return 0;
}

int Cli::add_cmd(const Args &args) {
  if (args.flag("--steam")) {
    wait_scan();
    scan_steam_games();
    wait([&] { return !steam_scanning_; });
    auto found = steam_games_found_;
    auto added = [&](const rift::Game &s) {
      return std::ranges::any_of(installs_, [&](auto &i) { return i.game.source == "steam" && i.game.app_key == s.app_key; });
    };
    std::vector<rift::Game> pick;
    if (args.flag("--all")) {
      pick = found;
    } else if (args.words.size() > 1) {
      const auto q = lower(args.words[1]);
      for (auto &s : found)
        if (s.app_id == args.words[1] || lower(s.name).find(q) != std::string::npos)
          pick.push_back(s);
      if (pick.size() != 1)
        throw std::runtime_error(pick.empty() ? std::format("No Steam game with an Oculus mode matches \"{}\".", args.words[1])
                                              : std::format("\"{}\" matches several Steam games; use its app id.", args.words[1]));
    } else {
      if (json_) {
        Json out = Json::array();
        for (auto &s : found)
          out.push_back({{"steamAppId", s.app_id}, {"name", s.name}, {"directory", paths::utf8(s.directory)},
                         {"added", added(s)}});
        return print(out), 0;
      }
      if (found.empty()) {
        std::cout << "No installed Steam game uses the Oculus PC runtime.\n";
        return 0;
      }
      std::vector<std::vector<std::string>> rows;
      for (auto &s : found)
        rows.push_back({s.name, added(s) ? "added" : "", s.app_id});
      table({"Steam game", "", "App ID"}, rows);
      std::cout << "\nAdd one: riftlift-cli add --steam GAME  (or --all)\n";
      return 0;
    }
    Json out = Json::array();
    for (auto &s : pick) {
      auto slug = add_steam_game(s);
      out.push_back({{"slug", slug}, {"name", s.name}});
      if (!json_)
        std::cout << "Added " << s.name << ".\n";
    }
    wait_scan();
    if (json_)
      print(out);
    return 0;
  }
  const auto &what = args.word(1, "an .exe or a store link");
  if (!meta::app_id_from_link(what).empty() && !fs::exists(paths::from_utf8(what))) {
    need_online();
    wait_library();
    owned_link_ = what;
    notice_.clear();
    add_owned();
    wait([&] { return !adding_; });
    if (!owned_link_.empty())
      throw std::runtime_error(notice_.empty() ? "Meta has no Rift build of that game for this account." : notice_);
    if (json_)
      return print({{"added", true}, {"message", notice_}}), 0;
    std::cout << notice_ << "\n";
    return 0;
  }
  wait_scan();
  auto slug = add_local_game(fs::absolute(paths::from_utf8(what)), args.value("--name").value_or(""),
                             args.value("--args").value_or(""));
  wait_scan();
  if (json_)
    return print({{"slug", slug}, {"added", true}}), 0;
  std::cout << notice_ << "\n";
  return 0;
}

int Cli::configure_cmd(const Args &args) {
  wait_scan();
  auto g = resolve_game(args.word(1, "game"));
  auto i = installed(g.id, false);
  if (!i)
    throw std::runtime_error(std::format("{} is not installed.", g.name));
  auto record = i->game;
  bool changed = false;
  if (auto o = args.value("--options")) {
    try {
      record.launch_options = rift::split_arguments(*o);
    } catch (const std::exception &e) {
      throw UsageError(std::format("--options: {}", e.what()));
    }
    changed = true;
  }
  if (auto d = args.value("--dll-overrides")) {
    record.dll_overrides = *d;
    changed = true;
  }
  if (args.flag("--clear-env")) {
    record.environment.clear();
    changed = true;
  }
  for (const auto &e : args.env) {
    auto eq = e.find('=');
    if (eq == std::string::npos || eq == 0)
      throw UsageError("--env takes NAME=VALUE (an empty VALUE removes NAME).");
    if (eq + 1 == e.size())
      record.environment.erase(e.substr(0, eq));
    else
      record.environment[e.substr(0, eq)] = e.substr(eq + 1);
    changed = true;
  }
  if (auto shim = args.value("--shim")) {
    if (*shim != "on" && *shim != "off")
      throw UsageError("--shim takes on or off.");
    record.platform_shim = *shim == "on";
    changed = true;
  }
  if (changed) {
    try {
      record.validate();
    } catch (const std::invalid_argument &e) {
      throw UsageError(e.what());
    }
    save_game_settings(record);
    wait_scan();
  }
  Json out{{"launchOptions", record.launch_options},
           {"dllOverrides", record.dll_overrides},
           {"environment", record.environment},
           {"platformShim", record.platform_shim}};
  if (json_)
    return print(out), 0;
  std::cout << "Options:        " << rift::join_arguments(record.launch_options) << "\n";
  std::cout << "DLL overrides:  " << record.dll_overrides << "\n";
  std::cout << "Platform shim:  " << (record.platform_shim ? "on" : "off") << "\n";
  for (auto &[k, v] : record.environment)
    std::cout << "Environment:    " << k << "=" << v << "\n";
  return 0;
}

int Cli::downloads_cmd(const Args &args) {
  const std::string sub = args.words.size() > 1 ? args.words[1] : "list";
  if (sub == "cancel" || sub == "resume") {
    need_queue();
    wait_scan();
    auto g = resolve_game(args.word(2, "game"));
    auto job = download::base_job(downloads_->snapshot(), g.id);
    if (!job)
      throw std::runtime_error(std::format("{} has no download in the queue.", g.name));
    if (sub == "cancel") {
      downloads_->cancel(g.id);
      if (!json_)
        std::cout << "Cancelled the download of " << g.name << ".\n";
      return 0;
    }
    if (options_.offline)
      throw std::runtime_error("Downloads need a connection. Run without --offline.");
    auto owner = job->account == "local" ? std::nullopt : vault_.load(job->account);
    if (!owner && job->account != "local")
      throw std::runtime_error("Reconnect the account this download belongs to.");
    downloads_->resume(*job, owner ? owner->access_token : std::string{});
    follow(g.id, "Downloading");
    wait_scan();
    rescan();
    wait_scan();
    if (!json_)
      std::cout << "Finished " << g.name << ".\n";
    return 0;
  }
  if (sub != "list")
    throw UsageError(std::format("Unknown downloads command \"{}\".", sub));
  auto jobs = downloads_->snapshot();
  if (json_) {
    Json out = Json::array();
    for (auto &j : jobs)
      out.push_back({{"id", j.app_id}, {"name", j.name}, {"state", job_state(j.state)}, {"done", j.done},
                     {"total", j.total}, {"error", j.error}, {"account", account_label(j.account)}});
    return print(out), 0;
  }
  if (jobs.empty()) {
    std::cout << "The download queue is empty.\n";
    return 0;
  }
  std::vector<std::vector<std::string>> rows;
  for (auto &j : jobs)
    rows.push_back({j.name, job_state(j.state),
                    j.total ? std::format("{} of {}", bytes(j.done), bytes(j.total)) : "", j.error});
  table({"Download", "State", "Progress", "Error"}, rows);
  return 0;
}

int Cli::accounts_cmd(const Args &args) {
  const std::string sub = args.words.size() > 1 ? args.words[1] : "list";
  if (sub == "list") {
    if (json_) {
      Json out = Json::array();
      for (auto &a : accounts_)
        out.push_back({{"key", a.key}, {"name", account_name(a)}, {"alias", a.alias}, {"active", a.active},
                       {"primary", a.primary}, {"signedIn", a.signed_in}, {"needsReconnect", a.needs_reauth}});
      return print(out), 0;
    }
    if (accounts_.empty()) {
      std::cout << "No accounts. Add one: riftlift-cli accounts sign-in\n";
      return 0;
    }
    std::vector<std::vector<std::string>> rows;
    for (size_t i = 0; i < accounts_.size(); ++i) {
      auto &a = accounts_[i];
      std::string state = !a.signed_in ? "signed out" : a.needs_reauth ? "needs reconnect" : "signed in";
      if (a.primary)
        state += ", primary";
      rows.push_back({std::string(a.active ? "*" : " ") + std::to_string(i + 1), account_name(a), a.alias, state,
                      a.key});
    }
    table({" #", "Account", "Alias", "Status", "Key"}, rows);
    return 0;
  }
  if (sub == "sign-in") {
    if (options_.offline)
      throw std::runtime_error("Sign-in needs a connection. Run without --offline.");
    notice_.clear();
    add_account();
    std::string shown;
    if (!json_)
      std::cout << "Complete sign-in in the Meta window.\n" << std::flush;
    wait([&] { return !signing_in_; },
         [&] {
           if (!json_ && auth_status_ != shown && !auth_status_.empty())
             std::cout << auth_status_ << "\n" << std::flush;
           shown = auth_status_;
         });
    if (!notice_.empty())
      throw std::runtime_error(notice_);
    if (!session_ || auth_status_ == "Sign-in cancelled.")
      throw std::runtime_error("Sign-in did not finish.");
    if (!json_)
      std::cout << "Signed in as " << account_label(account_key_) << ".\n";
    return 0;
  }
  const auto key = resolve_account(args.word(2, "account"));
  const auto name = account_label(key);
  if (sub == "use")
    switch_account(key);
  else if (sub == "primary")
    mark_primary(key);
  else if (sub == "sign-out")
    sign_out_account(key);
  else if (sub == "remove") {
    if (!confirm(args, std::format("Remove {} from RiftLift?", name)))
      return 1;
    remove_account(key);
  } else
    throw UsageError(std::format("Unknown accounts command \"{}\".", sub));
  if (!json_)
    std::cout << (sub == "use"        ? "Active account: " + name
                  : sub == "primary"  ? "Primary account: " + name
                  : sub == "sign-out" ? "Signed out " + name + ". Its games stay on this PC."
                                      : "Removed " + name + ".")
              << "\n";
  return 0;
}

constexpr std::string_view kLevels[] = {"trace", "debug", "info", "warn", "error"};

int Cli::settings_cmd(const Args &args) {
  const std::string sub = args.words.size() > 1 ? args.words[1] : "show";
  if (sub == "set") {
    const auto &key = args.word(2, "setting");
    const auto &value = args.word(3, "value");
    auto next = settings_;
    auto on_off = [&](const std::string &v) {
      if (v == "on" || v == "true" || v == "1")
        return true;
      if (v == "off" || v == "false" || v == "0")
        return false;
      throw UsageError(std::format("{} takes on or off.", key));
    };
    if (key == "games-folder")
      next.games = fs::absolute(paths::from_utf8(value)).lexically_normal();
    else if (key == "runtime")
      next.runtime = value == "default" ? library::Settings::defaults(data_).runtime
                                        : fs::absolute(paths::from_utf8(value)).lexically_normal();
    else if (key == "language") {
      if (value != "auto" && value != "en" && value != "fr")
        throw UsageError("language takes auto, en or fr.");
      next.language = value;
    }
    else if (key == "log-level") {
      const auto it = std::ranges::find(kLevels, lower(value));
      if (it == std::end(kLevels))
        throw UsageError("log-level takes trace, debug, info, warn or error.");
      next.log_level = int(it - std::begin(kLevels));
    } else if (key == "console")
      next.console = on_off(value);
    else if (key == "debug-logging")
      next.debug_logging = on_off(value);
    else if (key == "add-to-steam")
      next.add_games_to_steam = on_off(value);
    else if (key == "add-new-to-steam")
      next.steam_add_new = on_off(value);
    else
      throw UsageError(std::format("Unknown setting \"{}\".", key));
    apply_settings(std::move(next), false);
    wait_scan();
  } else if (sub != "show") {
    throw UsageError(std::format("Unknown settings command \"{}\".", sub));
  }
  Json out{{"games-folder", paths::utf8(settings_.games)},
           {"runtime", settings_.runtime_override() ? paths::utf8(settings_.runtime) : "default"},
           {"language", settings_.language},
           {"log-level", std::string(kLevels[std::clamp(settings_.log_level, 0, 4)])},
           {"console", settings_.console},
           {"debug-logging", settings_.debug_logging},
           {"add-to-steam", settings_.add_games_to_steam},
           {"add-new-to-steam", settings_.steam_add_new}};
  Json folders = Json::array();
  for (const auto &folder : settings_.libraries)
    folders.push_back(paths::utf8(folder));
  out["library-folders"] = std::move(folders);
  if (json_)
    return print(out), 0;
  for (auto &[k, v] : out.items())
    std::cout << std::format("{:<17}{}\n", k, v.is_string() ? v.get<std::string>() : v.is_boolean() ? (v.get<bool>() ? "on" : "off") : v.dump());
  return 0;
}

int Cli::run(const Args &args) {
  const auto &cmd = args.words.front();
  if (cmd == "status") return status(args);
  if (cmd == "library") return library_list(args);
  if (cmd == "info") return info(args);
  if (cmd == "install") return install_game(args);
  if (cmd == "uninstall") return uninstall_game(args);
  if (cmd == "play") return play_game(args);
  if (cmd == "logs") return logs(args);
  if (cmd == "report") return report(args);
  if (cmd == "add") return add_cmd(args);
  if (cmd == "configure") return configure_cmd(args);
  if (cmd == "downloads") return downloads_cmd(args);
  if (cmd == "accounts") return accounts_cmd(args);
  if (cmd == "settings") return settings_cmd(args);
  throw UsageError(std::format("Unknown command \"{}\".", cmd));
}

// Commands that read Meta's library start its refresh with the core.
bool needs_library(std::string_view cmd) {
  return cmd == "library" || cmd == "info" || cmd == "install" || cmd == "add";
}
} // namespace

int run_cli(const std::vector<std::string> &argv) {
  Args args;
  try {
    args = parse(argv);
  } catch (const UsageError &e) {
    std::cerr << "error: " << e.what() << "\n\n" << kUsage;
    return 2;
  }
  if (args.flag("--version")) {
    std::cout << "RiftLift " << RIFTLIFT_VERSION << "\n";
    return 0;
  }
  if (args.flag("--help") || args.words.empty() || args.words.front() == "help") {
    std::cout << kUsage;
    return args.words.empty() && !args.flag("--help") ? 2 : 0;
  }
  Options options;
  options.cli = true;
  options.automation = true;
  options.offline = args.flag("--offline");
  const auto &cmd = args.words.front();
  // Play is the launcher's direct launch: no library refresh, and the window's
  // download queue and catalog stay untouched.
  if (cmd == "play")
    options.launch_requested = true;
  std::signal(SIGINT, on_interrupt);
  std::signal(SIGTERM, on_interrupt);
#ifdef SIGBREAK
  std::signal(SIGBREAK, on_interrupt); // Ctrl+Break, or a harness stopping the process group
#endif
  // The core's warnings stay in logs/cli-<pid>.log; the terminal shows results.
  log::set_stderr(false);
  try {
    Cli cli(options, args.flag("--json"));
    cli.start(needs_library(cmd));
    return cli.run(args);
  } catch (const UsageError &e) {
    std::cerr << "error: " << e.what() << "\nSee: riftlift-cli --help\n";
    return 2;
  } catch (const Interrupted &) {
    std::cerr << "\nInterrupted. Queued downloads continue the next time RiftLift opens.\n";
    return 130;
  } catch (...) {
    std::cerr << "error: " << exception_text() << "\n";
    return 1;
  }
}
} // namespace riftlift::launcher

#ifdef _WIN32
int wmain(int argc, wchar_t **argv) {
  SetConsoleOutputCP(CP_UTF8);
  std::vector<std::string> args;
  for (int i = 0; i < argc; ++i)
    args.push_back(riftlift::paths::utf8(riftlift::fs::path(argv[i])));
  return riftlift::launcher::run_cli(args);
}
#else
int main(int argc, char **argv) {
  // Sign-in runs Meta's page in a helper process started from this executable.
  if (int helper = riftlift::launcher::auth_browser_helper(argc, argv); helper >= 0)
    return helper;
  return riftlift::launcher::run_cli({argv, argv + argc});
}
#endif
