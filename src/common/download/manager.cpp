#include "common/download/manager.h"

#include "common/digest.h"
#include "common/file_ops.h"
#include "common/log.h"
#include "common/meta/accounts.h"
#include "common/rift/game.h"
#include <algorithm>
#include <atomic>
#include <format>
#include <fstream>
#include <miniz.h>
#include <map>
#include <set>
namespace riftlift::download {
namespace {
bool same(const Job &a, const Job &b) {
  return a.app_id == b.app_id && a.account == b.account && a.pinned == b.pinned &&
         (!a.pinned || a.binary_id == b.binary_id);
}
// Marks a folder RiftLift is downloading into until manifest.json is written.
constexpr const char *kMarker = ".riftlift-download";
std::string file_sha256(const fs::path &file) {
  std::ifstream in(paths::io_path(file), std::ios::binary);
  if (!in)
    throw std::runtime_error("Could not read " + paths::utf8(file.filename()) + ".");
  return digest::sha256(in);
}
int default_workers() {
  auto cpus = int(std::thread::hardware_concurrency());
  return std::clamp(cpus * 2, 4, 32);
}
} // namespace
const char *label(State s) {
  switch (s) {
  case State::queued:
    return "Queued";
  case State::checking:
    return "Checking ownership and build";
  case State::downloading:
    return "Downloading";
  case State::paused:
    return "Paused";
  case State::failed:
    return "Needs attention";
  case State::complete:
    return "Installed";
  }
  return "";
}
std::optional<Job> base_job(const std::vector<Job> &jobs, std::string_view app) {
  for (const auto &job : jobs)
    if (job.app_id == app && !job.pinned)
      return job;
  return std::nullopt;
}
fs::path install_dir(const fs::path &root, std::string_view slug) {
  if (!rift::valid_slug(slug) || !root.is_absolute())
    throw std::runtime_error("Invalid game installation path.");
  auto dir = root / paths::from_utf8(slug);
  if (!paths::within(root, dir))
    throw std::runtime_error("Game path escapes the games directory.");
  return dir;
}
std::string inflate_segment(std::string_view compressed, size_t limit) {
  mz_stream stream{};
  if (mz_inflateInit(&stream) != MZ_OK)
    throw std::runtime_error("Could not start decompression.");
  struct End {
    mz_stream *s;
    ~End() { mz_inflateEnd(s); }
  } end{&stream};
  std::string out;
  std::string buffer(1 << 20, '\0');
  stream.next_in = reinterpret_cast<const unsigned char *>(compressed.data());
  stream.avail_in = mz_uint32(compressed.size());
  for (;;) {
    stream.next_out = reinterpret_cast<unsigned char *>(buffer.data());
    stream.avail_out = mz_uint32(buffer.size());
    int status = mz_inflate(&stream, MZ_NO_FLUSH);
    out.append(buffer.data(), buffer.size() - stream.avail_out);
    if (out.size() > limit)
      throw std::runtime_error("A segment inflated beyond its limit.");
    if (status == MZ_STREAM_END)
      return out;
    if (status != MZ_OK && !(status == MZ_BUF_ERROR && stream.avail_out == 0))
      throw std::runtime_error("Meta sent a damaged segment.");
    if (status == MZ_BUF_ERROR || (stream.avail_in == 0 && stream.avail_out != 0))
      throw std::runtime_error("Meta sent a truncated segment.");
  }
}
Manager::Manager(fs::path data, std::function<void()> wake, Services services, bool persistent)
    : data_(data), file_(persistent ? data / "downloads.json" : fs::path{}),
      segments_(data / "cache" / "segments"), wake_(std::move(wake)),
      services_(std::move(services)) {
  try {
    auto index = meta::AccountVault(data).index();
    active_account_ = index.active;
    auto saved = !file_.empty() && fs::exists(file_) ? paths::read_json(file_) : Json::object();
    if (field(saved, "jobs").is_array())
      for (auto &j : saved["jobs"]) {
        auto id = str(j, "appId");
        auto root = paths::from_utf8(str(j, "root"));
        if (!digits(id, 5) || !root.is_absolute())
          continue;
        auto e = std::make_shared<Entry>();
        e->job.app_id = id;
        e->job.name = str(j, "name");
        e->job.root = root;
        e->job.binary_id = str(j, "binaryId");
        e->job.package = str(j, "package");
        if (!e->job.package.empty() && !rift::valid_slug(e->job.package))
          continue;
        e->job.pinned = field(j, "pinned") == true;
        if (e->job.pinned && (!digits(e->job.binary_id) || e->job.package.empty()))
          continue;
        e->job.account = str(j, "account");
        if (!e->job.account.empty() && !meta::valid_account_key(e->job.account))
          continue;
        e->job.state = State::paused;
        e->job.generation = next_generation_++;
        jobs_.push_back(e);
      }
  } catch (...) {
    log::warn("downloads", "Could not read the previous queue.");
  }
  save_locked();
  for (unsigned i = 0; i < max_parallel; ++i)
    threads_.emplace_back([this](std::stop_token s) { run(s); });
}
Manager::~Manager() {
  cancel_all();
  for (auto &thread : threads_)
    thread.request_stop();
  cv_.notify_all();
  threads_.clear();
}
void Manager::set_parallel(unsigned jobs) {
  {
    std::lock_guard lock(mutex_);
    parallel_ = std::clamp(jobs, 1u, max_parallel);
  }
  cv_.notify_all();
}
void Manager::save_locked() {
  if (file_.empty()) return;
  Json j = Json::array();
  for (auto &e : jobs_)
    if (e->job.state != State::complete && !e->removed)
      j.push_back({{"appId", e->job.app_id},
                   {"name", e->job.name},
                   {"account", e->job.account},
                   {"root", paths::utf8(e->job.root)},
                   {"binaryId", e->job.binary_id},
                   {"package", e->job.package},
                   {"pinned", e->job.pinned}});
  try {
    paths::atomic_write(file_, Json{{"version", 3}, {"jobs", j}}.dump(2));
  } catch (...) {
    log::warn(
        "downloads",
        "Could not save download queue. Downloaded segments are retained.");
  }
}
void Manager::enqueue(std::string app, std::string name, fs::path root,
                      std::string token) {
  enqueue(std::move(app), std::move(name), std::move(root), std::move(token),
          active_key());
}
std::string Manager::active_key() const {
  std::lock_guard lock(mutex_);
  return active_account_;
}
void Manager::enqueue(std::string app, std::string name, fs::path root,
                      std::string token, std::string account) {
  Job job;
  job.app_id = std::move(app);
  job.name = std::move(name);
  job.root = std::move(root);
  job.account = std::move(account);
  enqueue_job(std::move(job), std::move(token));
}
void Manager::enqueue_version(std::string app, std::string binary_id, std::string package, std::string name,
                              fs::path root, std::string token, std::string account) {
  if (!digits(binary_id) || !rift::valid_slug(package))
    throw std::runtime_error("Invalid version to download.");
  Job job;
  job.app_id = std::move(app);
  job.binary_id = std::move(binary_id);
  job.package = std::move(package);
  job.name = std::move(name);
  job.root = std::move(root);
  job.account = std::move(account);
  job.pinned = true;
  enqueue_job(std::move(job), std::move(token));
}
void Manager::enqueue_job(Job job, std::string token) {
  if (!meta::valid_account_key(job.account))
    throw std::runtime_error("Select a signed-in account before downloading.");
  if (token.empty())
    throw std::runtime_error("Sign in before resuming downloads.");
  if (!digits(job.app_id, 5) || !job.root.is_absolute())
    throw std::runtime_error("Invalid download destination.");
  std::lock_guard lock(mutex_);
  for (auto &e : jobs_)
    if (same(e->job, job)) {
      if (e->removed)
        throw std::runtime_error(
            "The cancelled download is still stopping. Try again shortly.");
      if (e->job.state == State::queued || e->job.state == State::checking ||
          e->job.state == State::downloading)
        return;
      e->job.state = State::queued;
      e->job.generation = next_generation_++;
      e->job.error.clear();
      e->job.done = e->job.total = 0;
      e->job.bytes_per_second = 0;
      e->job.root = std::move(job.root);
      e->job.name = std::move(job.name);
      e->token = std::move(token);
      e->cancel = std::stop_source{};
      save_locked();
      cv_.notify_all();
      wake_();
      return;
    }
  auto e = std::make_shared<Entry>();
  e->job = std::move(job);
  e->job.generation = next_generation_++;
  e->job.state = State::queued;
  e->token = std::move(token);
  jobs_.push_back(e);
  save_locked();
  cv_.notify_all();
  wake_();
}
void Manager::cancel(std::string_view app) {
  std::lock_guard lock(mutex_);
  for (auto &e : jobs_)
    if (e->job.app_id == app && !e->job.pinned && visible(e->job)) {
      e->cancel.request_stop();
      if (e->job.state == State::queued) {
        e->job.state = State::paused;
        e->token.clear();
      }
    }
  save_locked();
  wake_();
}
void Manager::remove(const Job &job, bool discard) {
  std::shared_ptr<Entry> gone;
  {
    std::lock_guard lock(mutex_);
    auto it = std::ranges::find_if(
        jobs_, [&](auto &e) { return !e->removed && same(e->job, job); });
    if (it == jobs_.end())
      return;
    auto e = *it;
    e->cancel.request_stop();
    e->token.clear();
    e->removed = true;
    e->discard = discard;
    if (e->job.state != State::checking && e->job.state != State::downloading) {
      jobs_.erase(it);
      gone = e;
    }
    save_locked();
  }
  if (gone && gone->discard)
    discard_files(gone->job);
  wake_();
}
void Manager::release(const std::shared_ptr<Entry> &e) {
  // The job's slot is free for another worker.
  cv_.notify_all();
  {
    std::lock_guard lock(mutex_);
    if (!e->removed)
      return;
    std::erase(jobs_, e);
    save_locked();
  }
  if (e->discard)
    discard_files(e->job);
  wake_();
}
void Manager::discard_files(const Job &job) {
  // Only a folder still marked as an unfinished download: an installed game
  // being updated keeps its files.
  try {
    if (rift::valid_slug(job.package)) {
      auto dir = install_dir(job.root, job.package);
      std::error_code ec;
      if (fs::exists(dir / kMarker, ec) && !fs::exists(dir / "manifest.json", ec))
        remove_tree(job.root, dir);
    }
  } catch (const std::exception &error) {
    log::warn("downloads", "Could not delete the partial files of {}: {}",
              job.app_id, error.what());
  }
  std::lock_guard lock(mutex_);
  ++discarded_;
}
std::uint64_t Manager::discarded() const {
  std::lock_guard lock(mutex_);
  return discarded_;
}
void Manager::move(const Job &job, int step) {
  std::lock_guard lock(mutex_);
  std::vector<std::size_t> shown;
  std::optional<std::size_t> at;
  for (std::size_t i = 0; i < jobs_.size(); ++i)
    if (visible(jobs_[i]->job) && !jobs_[i]->removed) {
      if (same(jobs_[i]->job, job))
        at = shown.size();
      shown.push_back(i);
    }
  if (!at || std::int64_t(*at) + step < 0 ||
      std::int64_t(*at) + step >= std::int64_t(shown.size()))
    return;
  std::swap(jobs_[shown[*at]], jobs_[shown[*at + step]]);
  save_locked();
  wake_();
}
void Manager::resume(const Job &job, std::string token) {
  if (job.pinned)
    enqueue_version(job.app_id, job.binary_id, job.package, job.name, job.root, std::move(token), active_key());
  else
    enqueue(job.app_id, job.name, job.root, std::move(token));
}
void Manager::cancel_all() {
  std::lock_guard lock(mutex_);
  for (auto &e : jobs_) {
    e->cancel.request_stop();
    if (e->job.state == State::queued)
      e->job.state = State::paused;
    e->token.clear();
  }
  save_locked();
  wake_();
}
std::vector<Job> Manager::snapshot() const {
  std::lock_guard lock(mutex_);
  std::vector<Job> out;
  for (auto &e : jobs_)
    if (visible(e->job) && !e->removed)
      out.push_back(e->job);
  return out;
}
std::vector<Job> Manager::snapshot(std::string_view account) const {
  std::lock_guard lock(mutex_);
  std::vector<Job> out;
  for (auto &e : jobs_)
    if (e->job.account == account && !e->removed)
      out.push_back(e->job);
  return out;
}
void Manager::set_account(std::string key) {
  if (!key.empty() && !meta::valid_account_key(key))
    throw std::runtime_error("Invalid account key.");
  std::lock_guard lock(mutex_);
  active_account_ = std::move(key);
}
void Manager::adopt_legacy(std::string_view primary) {
  if (!meta::valid_account_key(primary))
    throw std::runtime_error("Invalid primary account key.");
  std::lock_guard lock(mutex_);
  for (auto &e : jobs_)
    if (e->job.account.empty())
      e->job.account = primary;
  save_locked();
}
void Manager::cancel_account(std::string_view key) {
  std::lock_guard lock(mutex_);
  for (auto &e : jobs_)
    if (e->job.account == key) {
      e->cancel.request_stop();
      if (e->job.state == State::queued)
        e->job.state = State::paused;
      e->token.clear();
    }
  save_locked();
  wake_();
}
bool Manager::busy() const {
  std::lock_guard lock(mutex_);
  return std::ranges::any_of(jobs_, [](auto &e) {
    return e->job.state == State::queued || e->job.state == State::checking ||
           e->job.state == State::downloading;
  });
}
void Manager::install(Entry &e, meta::Store &store, std::stop_token stop) {
  auto plan = store.owned_plan(e.job.app_id, e.job.binary_id);
  {
    std::lock_guard lock(mutex_);
    // An earlier install keeps its folder name; a new one is named for the game.
    if (e.job.package.empty())
      e.job.package = plan.package;
    plan.package = e.job.package;
    if (e.job.name.empty())
      e.job.name = plan.name;
    if (e.job.pinned)
      plan.name = e.job.name;  // "Lone Echo (3.17.4)"
    save_locked();
  }
  // Updating an installed game rebuilds it where it is.
  fs::path dir;
  try {
    auto record = rift::load(data_, plan.package);
    if (record.source == "meta" && record.directory.is_absolute())
      dir = record.directory;
  } catch (...) {
  }
  if (dir.empty())
    dir = install_dir(e.job.root, plan.package);
  if (stop.stop_requested())
    throw std::runtime_error("Transfer paused.");
  fs::create_directories(dir);
  if (!fs::exists(dir / "manifest.json"))
    paths::atomic_write(dir / kMarker, plan.binary_id);
  // Files already present with the right hash stay; the rest are rebuilt.
  std::vector<const meta::File *> needed;
  uint64_t remaining = 0;
  for (const auto &f : plan.files) {
    auto target = dir / paths::from_utf8(f.name);
    if (!paths::within(dir, target))
      throw std::runtime_error("A file path escapes the game folder.");
    std::error_code ec;
    if (fs::is_regular_file(target, ec) && fs::file_size(target, ec) == f.size && file_sha256(target) == f.sha256)
      continue;
    needed.push_back(&f);
    remaining += f.size;
  }
  std::map<std::string, uint64_t> segments;  // sha -> compressed size
  for (const auto *f : needed)
    for (const auto &s : f->segments)
      segments.emplace(s.sha256, s.compressed);
  fs::create_directories(segments_);
  uint64_t download_total = 0;
  bool sized = std::ranges::all_of(segments, [](auto &s) { return s.second > 0; });
  std::vector<std::string> fetch;
  for (const auto &[sha, compressed] : segments) {
    std::error_code ec;
    if (fs::is_regular_file(segments_ / sha, ec))
      continue;
    fetch.push_back(sha);
    download_total += sized ? compressed : 1;
  }
  // Segments stay in the cache until the whole build is assembled, so a cache
  // on the games folder's disk needs room for both at the peak.
  constexpr uint64_t kMargin = 256 * 1024 * 1024;
  const uint64_t fetched = sized ? download_total : 0;
  const auto games_space = fs::space(dir), cache_space = fs::space(segments_);
  const bool shared = games_space.capacity == cache_space.capacity && games_space.free == cache_space.free;
  const uint64_t games_needed = remaining + (shared ? fetched : 0);
  if (games_space.available < games_needed + kMargin)
    throw std::runtime_error(std::format("Not enough free space: {:.1f} GiB needed. Free disk space or choose "
                                         "another games folder in Settings.", double(games_needed) / (1ull << 30)));
  if (!shared && cache_space.available < fetched + kMargin)
    throw std::runtime_error(std::format("Not enough free space for the download: {:.1f} GiB needed in {}. Free "
                                         "disk space there.", double(fetched) / (1ull << 30),
                                         paths::utf8(segments_.parent_path())));
  {
    std::lock_guard lock(mutex_);
    e.job.state = State::downloading;
    e.job.phase = "Downloading";
    e.job.total = download_total;
    e.job.done = 0;
  }
  wake_();
  // Segments download in parallel; each is checked before it enters the cache.
  std::atomic<size_t> next{0};
  std::atomic<uint64_t> received{0};
  std::mutex failure_mutex;
  std::string failure;
  std::stop_source abort;
  std::stop_callback forward(stop, [&] { abort.request_stop(); });
  const auto start = std::chrono::steady_clock::now();
  auto worker = [&] {
    for (size_t i; (i = next++) < fetch.size() && !abort.stop_requested();) {
      const auto &sha = fetch[i];
      try {
        std::string clear;
        for (int attempt = 0;; ++attempt) {
          try {
            clear = inflate_segment(store.segment(plan.binary_id, sha), size_t(1) << 28);
            if (digest::sha256(std::span(reinterpret_cast<const std::uint8_t *>(clear.data()), clear.size())) != sha)
              throw std::runtime_error("Segment " + sha.substr(0, 12) + " failed its hash check.");
            break;
          } catch (const meta::RequestError &error) {
            if ((error.status != 429 && error.status < 500) || attempt == 4 || abort.stop_requested())
              throw;
          } catch (...) {
            if (attempt == 4 || abort.stop_requested())
              throw;
          }
          std::mutex m;
          std::condition_variable_any cv;
          std::unique_lock lock(m);
          cv.wait_for(lock, abort.get_token(), std::chrono::seconds(std::min(1 << attempt, 30)), [] { return false; });
        }
        auto part = segments_ / (sha + ".part" + std::to_string(i));
        paths::atomic_write(part, clear);
        std::error_code ec;
        fs::rename(part, segments_ / sha, ec);
        if (ec)
          throw std::runtime_error("Could not store a downloaded segment.");
        received += sized ? segments.at(sha) : 1;
        {
          std::lock_guard lock(mutex_);
          e.job.done = received;
          auto seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
          e.job.bytes_per_second = sized && seconds > 0.5 ? double(received) / seconds : 0;
        }
        wake_();
      } catch (...) {
        std::lock_guard lock(failure_mutex);
        if (failure.empty() && !stop.stop_requested()) {
          try {
            throw;
          } catch (const std::exception &error) {
            failure = error.what();
          } catch (...) {
            failure = "A segment download failed.";
          }
        }
        abort.request_stop();
      }
    }
  };
  {
    const int count = services_.workers > 0 ? services_.workers : default_workers();
    std::vector<std::jthread> workers;
    for (int i = 0; i < count && i < int(fetch.size()); ++i)
      workers.emplace_back(worker);
  }
  if (stop.stop_requested())
    throw std::runtime_error("Transfer paused.");
  if (!failure.empty())
    throw std::runtime_error(failure);
  {
    std::lock_guard lock(mutex_);
    e.job.phase = "Assembling files";
    e.job.total = remaining;
    e.job.done = 0;
    e.job.bytes_per_second = 0;
  }
  wake_();
  uint64_t assembled = 0;
  auto last = std::chrono::steady_clock::now();
  for (const auto *f : needed) {
    if (stop.stop_requested())
      throw std::runtime_error("Transfer paused.");
    auto target = dir / paths::from_utf8(f->name);
    fs::create_directories(target.parent_path());
    auto part = target;
    part += ".part";
    {
      std::lock_guard lock(mutex_);
      e.job.file = f->name;
    }
    digest::Sha256 hash;
    uint64_t written = 0;
    {
      std::ofstream out(paths::io_path(part), std::ios::binary | std::ios::trunc);
      std::string chunk(4 << 20, '\0');
      for (const auto &s : f->segments) {
        std::ifstream in(paths::io_path(segments_ / s.sha256), std::ios::binary);
        if (!in)
          throw std::runtime_error("A downloaded segment is missing. Resume to fetch it again.");
        while (in) {
          in.read(chunk.data(), std::streamsize(chunk.size()));
          auto got = size_t(in.gcount());
          if (!got)
            break;
          out.write(chunk.data(), std::streamsize(got));
          hash.update(std::span(reinterpret_cast<const std::uint8_t *>(chunk.data()), got));
          written += got;
        }
      }
      if (!out.flush())
        throw std::runtime_error("Could not write " + f->name + ". Check free space.");
    }
    if (written != f->size || hash.finish() != f->sha256) {
      std::error_code ec;
      fs::remove(part, ec);
      // A bad cached segment is fetched again on resume.
      for (const auto &s : f->segments)
        fs::remove(segments_ / s.sha256, ec);
      throw std::runtime_error("Validation failed while assembling " + f->name + ". Resume to retry.");
    }
    fs::rename(part, target);
    assembled += f->size;
    auto now = std::chrono::steady_clock::now();
    {
      std::lock_guard lock(mutex_);
      e.job.done = assembled;
    }
    if (now - last > std::chrono::milliseconds(100)) {
      last = now;
      wake_();
    }
  }
  // Meta's manifest goes in last: it marks a complete install.
  auto manifest = store.manifest(plan.binary_id);
  paths::atomic_write(dir / "manifest.json", manifest.dump(2) + "\n");
  std::error_code ec;
  fs::remove(dir / kMarker, ec);
  if (services_.installed)
    services_.installed({plan, dir, e.job.pinned});
  // The build is assembled; its segments only took space.
  for (const auto &[sha, size] : segments)
    fs::remove(segments_ / sha, ec);
}
void Manager::run(std::stop_token shutdown) {
  while (!shutdown.stop_requested()) {
    std::shared_ptr<Entry> e;
    std::string token;
    std::stop_token stop;
    {
      std::unique_lock lock(mutex_);
      // The first queued job in list order whose game has no running job,
      // while fewer than parallel_ run.
      auto next = [&]() -> std::shared_ptr<Entry> {
        const auto running = [](const auto &x) {
          return x->job.state == State::checking || x->job.state == State::downloading;
        };
        if (std::size_t(std::ranges::count_if(jobs_, running)) >= parallel_)
          return nullptr;
        for (auto &x : jobs_)
          if (x->job.state == State::queued &&
              std::ranges::none_of(jobs_, [&](auto &y) { return running(y) && y->job.app_id == x->job.app_id; }))
            return x;
        return nullptr;
      };
      cv_.wait(lock, shutdown, [&] { return (e = next()) != nullptr; });
      if (shutdown.stop_requested() || !e)
        return;
      e->job.state = State::checking;
      e->job.phase.clear();
      token = std::exchange(e->token, {});
      stop = e->cancel.get_token();
    }
    struct Release {
      Manager &manager;
      std::shared_ptr<Entry> entry;
      ~Release() { manager.release(entry); }
    } release{*this, e};
    wake_();
    try {
      if (token.empty())
        throw std::runtime_error("Sign in before resuming downloads.");
      meta::Store store(std::move(token), stop, services_.query, services_.read);
      install(*e, store, stop);
      {
        std::lock_guard lock(mutex_);
        e->job.state = State::complete;
        e->job.done = e->job.total;
        e->job.bytes_per_second = 0;
        e->job.file.clear();
        save_locked();
      }
      log::info("downloads", "Installed Rift application {}.", e->job.app_id);
    } catch (const fs::filesystem_error &error) {
      std::lock_guard lock(mutex_);
      log::warn("downloads", "{}: {}", e->job.app_id, error.what());
      e->job.state = stop.stop_requested() ? State::paused : State::failed;
      e->job.error = "Could not access the download folder. Check free space "
                     "and permissions, then resume.";
      save_locked();
    } catch (const Json::exception &) {
      std::lock_guard lock(mutex_);
      e->job.state = State::failed;
      e->job.error = "Meta returned unexpected build details. Refresh the "
                     "library and retry.";
      save_locked();
    } catch (const std::exception &error) {
      std::lock_guard lock(mutex_);
      e->job.state = stop.stop_requested() ? State::paused : State::failed;
      e->job.error = stop.stop_requested()
                         ? "Downloaded segments saved. Resume to continue."
                         : error.what();
      e->job.bytes_per_second = 0;
      save_locked();
      if (!stop.stop_requested())
        log::warn("downloads", "Download of {} failed: {}", e->job.app_id, error.what());
    }
    wake_();
  }
}
} // namespace riftlift::download
