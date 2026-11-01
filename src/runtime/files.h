#pragma once
// File helpers for installing payloads: checksums, atomic writes, staging
// folders swapped into place, verified downloads.
#include "common/paths.h"
#include <stop_token>
#include <string>

namespace riftlift::runtime {

// Lower-case hex SHA-256 of a file; throws when it cannot be read.
std::string sha256_file(const fs::path &file);
// Empty when the file cannot be read.
std::string try_sha256_file(const fs::path &file);
std::string read_file(const fs::path &file);
// Replaces `target` atomically through a unique sibling file; `mode` is the
// POSIX permission of the result (ignored on Windows).
void write_file(const fs::path &target, std::string_view bytes,
                fs::perms mode = fs::perms(0600));
// Copies preserving the file time (shutil.copy2), replacing `target`.
void copy_preserving(const fs::path &source, const fs::path &target);

// A uniquely named folder in `parent`, removed (with its contents) when this
// goes out of scope.
class Staging {
public:
  Staging(const fs::path &parent, std::string_view prefix);
  ~Staging();
  Staging(const Staging &) = delete;
  Staging &operator=(const Staging &) = delete;
  const fs::path &path() const { return path_; }

private:
  fs::path path_;
};

// Moves `source` to `destination`, keeping the previous `destination` until
// the move succeeded so a failure restores it.
void replace_directory(const fs::path &source, const fs::path &destination);

enum class Origin { meta, release };
// Downloads `url` to `target` unless it is already there (and matches
// `sha256` when one is given), verifying before it is published. Meta
// binaries come only from Meta's delivery hosts, release files only from
// GitHub releases. Retries 4 times.
fs::path fetch(const std::string &url, const fs::path &target, std::string_view sha256,
               Origin origin, std::stop_token stop);

} // namespace riftlift::runtime
