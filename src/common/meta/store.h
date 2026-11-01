#pragma once
#include "common/meta/auth.h"
#include <set>
namespace riftlift::meta {
struct Game {
  std::string id, name, description, publisher, image, platform,
      ownership_account;
  std::string package;  // the game's slug (its record and folder name)
  std::string canonical;  // Meta's canonical name, e.g. "b4t-games-epic-roller-coaster"
  std::string icon;     // the listing's square app icon; not cached in library.json
  bool owned = false;
  bool system = false;  // a Rift runtime component or home environment
  uint64_t recent = 0;
};
struct Snapshot {
  std::vector<Game> games;
  std::string account_id, alias, display_name, warning;
  bool complete = false;
};
// One file of a Rift build: rebuilt by joining its segments in order.
struct File {
  std::string name;  // '/' separated, relative to the game folder
  std::string sha256;
  uint64_t size = 0;
  struct Segment {
    std::string sha256;
    uint64_t compressed = 0;
  };
  std::vector<Segment> segments;
};
// A Rift build as Meta's manifest describes it.
struct Plan {
  std::string app_id, binary_id, package, version, version_code;
  std::string name, canonical, launch_file, launch_parameters;
  std::vector<File> files;
  // A game RiftLift did not download (a local or Steam game).
  bool local = false;
  uint64_t size() const;
};
// Meta's manifest.json for a build (from the binaries/download zip).
Plan parse_manifest(std::string_view app_id, std::string_view binary_id, const Json &manifest);
Json nodes(const Json &j);
bool rift_app(const Json &item);
Game card(const Json &item);
Snapshot parse_library(const Json &response);
// The 16:9 landscape cover named by a public store page: its schema.org
// primaryImageOfPage, else its og:image. Empty when the page names neither.
std::string cover_from_page(std::string_view html);
// A Meta store link or app id -> the app id; empty when there is none.
std::string app_id_from_link(std::string_view link);
// Meta lists no Rift application or build of this app to the account.
class NotListed : public std::runtime_error {
public:
  using std::runtime_error::runtime_error;
};
struct Build {
  std::string binary_id, version, version_code, change_log;
};
struct Details {
  Game game;
  std::string live_id, version, version_code;
  uint64_t size = 0;
};
class Store {
public:
  using Transport = std::function<Json(std::string_view, const http::Fields &)>;
  using ReadTransport =
      std::function<http::Response(std::string_view, const http::Fields &)>;
  using ListingTransport = std::function<Json(std::string_view)>;
  explicit Store(std::string token, std::stop_token stop = {},
                 Transport transport = {}, ReadTransport read = {},
                 ListingTransport listing = {})
      : token_(std::move(token)), stop_(stop), transport_(std::move(transport)),
        read_(std::move(read)), listing_(std::move(listing)) {}
  Json query(std::string_view doc, const Json &vars = Json::object()) const;
  // The signed-in account alone (id, alias, display name), without the
  // library: sign-in does not depend on listing every entitlement.
  Snapshot identity() const;
  Snapshot library() const;
  // The public listing plus, signed in, the newest Rift build.
  Details details(std::string_view id) const;
  Game artwork(std::string_view id) const;
  // The landscape cover from the public store page. Graph only offers the
  // wide hero banner and the square icon.
  std::string cover(std::string_view id) const;
  // Rift (PCBinary) builds, newest first, and the app's display name.
  std::vector<Build> builds(std::string_view id, std::string *name = nullptr) const;
  // One app as this account sees it: the app alone, owned, when it is in the
  // account's library; no games when it is not.
  Snapshot owned_listing(std::string_view id) const;
  // The newest build (or `binary_id`) with its file list, from Meta's manifest.
  Plan owned_plan(std::string_view id, std::string_view binary_id = {}) const;
  // The raw manifest.json of a build.
  Json manifest(std::string_view binary_id) const;
  // A segment's zlib stream; inflate it and check its SHA-256.
  std::string segment(std::string_view binary_id, std::string_view sha256) const;

private:
  std::string token_;
  std::stop_token stop_;
  Transport transport_;
  ReadTransport read_;
  ListingTransport listing_;
  Json public_item(std::string_view id) const;
  Json request(std::string_view url, const http::Fields &fields) const;
  http::Response fetch(std::string_view url, size_t limit) const;
};
} // namespace riftlift::meta
