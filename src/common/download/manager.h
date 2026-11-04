#pragma once
#include "common/meta/store.h"
#include <condition_variable>
#include <mutex>
#include <thread>
namespace riftlift::download {
enum class State { queued, checking, downloading, paused, failed, complete };
const char *label(State state);
struct Job {
  std::string app_id, name, file, error;
  fs::path root;  // the library folder the game goes into
  State state = State::paused;
  uint64_t done = 0, total = 0;
  double bytes_per_second = 0;
  std::string binary_id;  // empty: the newest build
  // A version chosen by hand: binary_id into its own folder (package), kept
  // beside the game's other versions; one job per version.
  bool pinned = false;
  std::string account;
  std::string package;    // the game's slug once known
  // "Downloading" fetches segments; "Assembling files" rebuilds and checks them.
  std::string phase;
  std::uint64_t generation = 1;
};
// The job of an app in a snapshot (not one of its chosen versions).
std::optional<Job> base_job(const std::vector<Job> &jobs, std::string_view app);
// How a finished Rift build becomes a game record.
struct Installed {
  meta::Plan plan;
  fs::path directory;
  bool pinned = false;
};
class Manager {
public:
  struct Services {
    meta::Store::Transport query;
    meta::Store::ReadTransport read;
    // Writes the game's record once its files are in place (the launcher's
    // library knows the store listing and the previous record).
    std::function<void(const Installed &)> installed;
    int workers = 0;  // parallel segment downloads; 0 picks from the CPU count
  };
  // A non-persistent manager keeps no downloads.json (a direct launch beside
  // an open launcher).
  Manager(fs::path data, std::function<void()> wake, Services services, bool persistent = true);
  Manager(fs::path data, std::function<void()> wake) : Manager(std::move(data), std::move(wake), Services{}) {}
  ~Manager();
  void enqueue(std::string app, std::string name, fs::path root,
               std::string token);
  void enqueue(std::string app, std::string name, fs::path root,
               std::string token, std::string account);
  // One chosen version (`binary_id`) of a game into root/package, named `name`.
  void enqueue_version(std::string app, std::string binary_id, std::string package, std::string name,
                       fs::path root, std::string token, std::string account);
  void cancel(std::string_view app);
  // Drops a job from the queue, stopping it first. With `discard`, the files
  // of a game that never finished installing are deleted too.
  void remove(const Job &job, bool discard);
  // Moves a job one place earlier (-1) or later (+1) among the visible jobs;
  // queued jobs start in this order.
  void move(const Job &job, int step);
  // Counts the partial installs remove() deleted; callers rescan on a change.
  std::uint64_t discarded() const;
  void resume(const Job &job, std::string token);
  void cancel_all();
  void cancel_account(std::string_view key);
  void set_account(std::string key);
  void adopt_legacy(std::string_view primary);
  std::vector<Job> snapshot(std::string_view account) const;
  std::vector<Job> snapshot() const;
  bool busy() const;
  // How many jobs transfer at once (1 to max_parallel); jobs of one game
  // always run one after another.
  void set_parallel(unsigned jobs);
  static constexpr unsigned max_parallel = 4;

private:
  struct Entry {
    Job job;
    std::string token;
    std::stop_source cancel;
    // Set by remove(); a running job leaves when its worker releases it.
    bool removed = false, discard = false;
  };
  void enqueue_job(Job job, std::string token);
  mutable std::mutex mutex_;
  std::condition_variable_any cv_;
  std::vector<std::shared_ptr<Entry>> jobs_;
  fs::path data_, file_, segments_;
  std::string active_account_;
  std::uint64_t next_generation_ = 1, discarded_ = 0;
  std::function<void()> wake_;
  unsigned parallel_ = 1;
  std::vector<std::jthread> threads_;
  Services services_;
  std::jthread thread_;
  std::string active_key() const;
  void run(std::stop_token stop);
  // `binary`: the build to install; empty takes the job's own (its pin, or the newest).
  void install(Entry &e, meta::Store &store, std::stop_token stop, std::string_view binary = {});
  // install(), and when Meta has dropped the files of the newest build (HTTP
  // 410), the next build it offers, unless the job asked for that version.
  void install_available(Entry &e, meta::Store &store, std::stop_token stop);
  bool visible(const Job &job) const { return job.account == active_account_; }
  void save_locked();
  void release(const std::shared_ptr<Entry> &e);
  void discard_files(const Job &job);
};
// The folder a store game downloads to.
fs::path install_dir(const fs::path &root, std::string_view slug);
// Inflates a segment's zlib stream; throws when it is not one.
std::string inflate(std::string_view compressed, size_t limit = 256 * 1024 * 1024);
} // namespace riftlift::download
