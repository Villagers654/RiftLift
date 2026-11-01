#pragma once
// Extracting downloaded payloads without letting a member escape its folder.
#include "common/paths.h"
#include <stop_token>
#include <string>

namespace riftlift::runtime {

// A member name that stays inside the extraction folder: relative, no drive,
// and not climbing above it once normalized ("a/../b" is fine). Backslashes
// count as separators, as Python's zip extraction treated them.
bool archive_member_safe(std::string_view name);

// Extracts every member of a zip archive below `destination`.
void extract_zip(const fs::path &archive, const fs::path &destination);
// One member's bytes; throws when it is missing.
std::string zip_entry(const fs::path &archive, std::string_view name);

#ifndef _WIN32
// Extracts a tar archive (any compression the system tar reads) below
// `destination` after checking every member: devices are refused, and links
// unless `allow_links`.
void extract_tar(const fs::path &archive, const fs::path &destination, bool allow_links,
                 std::stop_token stop);
// "-rw-r--r-- user/group ..." listing lines to member types ('-', 'd', 'l', 'h', ...).
std::string tar_member_types(std::string_view verbose_listing);
#endif

} // namespace riftlift::runtime
