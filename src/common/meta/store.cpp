#include "common/meta/store.h"
#include "common/log.h"
#include "common/rift/game.h"
#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cctype>
#include <map>
#include <miniz.h>
#include <mutex>
#include <regex>
#include <set>
namespace riftlift::meta {
namespace {
constexpr std::string_view kLibraryDoc = "4850747515044496";
constexpr std::string_view kBuildsDoc = "1586217024733717";
// Waits `delay` unless a stop is requested first; false when stopped.
bool pause_for(std::stop_token stop, std::chrono::milliseconds delay) {
  std::mutex mutex;
  std::condition_variable_any wake;
  std::unique_lock lock(mutex);
  wake.wait_for(lock, stop, delay, [] { return false; });
  return !stop.stop_requested();
}
void require_id(std::string_view id) {
  if (!digits(id, 5))
    throw std::runtime_error("Invalid Meta application ID.");
}
std::string upper_ascii(std::string text) {
  for (char &letter : text)
    letter = char(std::toupper(static_cast<unsigned char>(letter)));
  return text;
}
const Json &connection(const Json &response) {
  const auto &viewer = field(field(response, "data"), "viewer");
  const auto &owner = field(viewer, "user");
  if (!field(owner, "active_entitlements").is_null())
    return owner["active_entitlements"];
  return field(viewer, "active_entitlements");
}
bool sha256_hex(std::string_view text) {
  return text.size() == 64 && text.find_first_not_of("0123456789abcdef") == text.npos;
}
// Manifest paths use either separator; none may leave the game folder or
// collide with RiftLift's own files.
std::string safe_relative(std::string name) {
  std::ranges::replace(name, '\\', '/');
  if (name.empty() || name.front() == '/' || name.size() > 1024)
    throw std::runtime_error("Unsafe path in the build manifest: " + name);
  size_t start = 0;
  while (start <= name.size()) {
    auto end = name.find('/', start);
    auto part = name.substr(start, end == name.npos ? name.npos : end - start);
    if (!paths::safe_name(part) || part == "." || part == "..")
      throw std::runtime_error("Unsafe path in the build manifest: " + name);
    start = end == name.npos ? name.size() + 1 : end + 1;
  }
  auto lower = name;
  std::ranges::transform(lower, lower.begin(), [](unsigned char c) { return char(std::tolower(c)); });
  if (lower.ends_with(".part") || lower == "manifest.json.tmp")
    throw std::runtime_error("The build manifest names a file RiftLift reserves: " + name);
  return name;
}
} // namespace

Json nodes(const Json &j) {
  if (j.is_array())
    return j;
  auto &n = field(j, "nodes");
  if (n.is_array())
    return n;
  auto &edges = field(j, "edges");
  Json result = Json::array();
  if (edges.is_array())
    for (auto &edge : edges) {
      if (!field(edge, "node").is_object())
        throw std::runtime_error(
            "Meta returned an incomplete list. Previous data was kept.");
      result.push_back(edge["node"]);
    }
  else
    throw std::runtime_error(
        "Meta returned an invalid list. Previous data was kept.");
  return result;
}
bool rift_app(const Json &item) { return upper_ascii(str(item, "platform")) == "PC"; }
Game card(const Json &j) {
  Game g;
  g.id = str(j, "id");
  g.name = str(j, "display_name");
  if (g.name.empty())
    g.name = str(j, "displayName");
  if (g.name.empty())
    g.name = str(j, "name");
  if (g.name.empty())
    g.name = "Rift app " + g.id;
  g.description = str(j, "display_long_description");
  if (g.description.empty())
    g.description = str(j, "display_short_description");
  g.publisher = str(j, "publisher_name");
  if (g.publisher.empty())
    g.publisher = str(j, "developer_name");
  for (auto key :
       {"cover_landscape_image", "hero", "cover_square_image", "icon_image"}) {
    g.image = str(field(j, key), "uri");
    if (!g.image.empty())
      break;
  }
  for (auto key : {"icon_image", "cover_square_image"}) {
    g.icon = str(field(j, key), "uri");
    if (!g.icon.empty())
      break;
  }
  g.platform = upper_ascii(str(j, "platform"));
  g.canonical = str(j, "canonical_name");
  g.package = rift::slugify(g.name);
  return g;
}
Snapshot parse_library(const Json &response) {
  Snapshot s;
  auto &c = connection(response);
  auto list = nodes(c);
  std::set<std::string> seen;
  for (auto &e : list) {
    auto &item = field(e, "item");
    auto id = str(item, "id");
    if (!item.is_object() || !digits(id, 5))
      throw std::runtime_error(
          "Meta returned incomplete entitlements. Previous library was kept.");
    if (rift_app(item) && seen.insert(id).second) {
      auto g = card(item);
      g.owned = true;
      s.games.push_back(g);
    }
  }
  auto &page = field(c, "page_info");
  if (c.is_object() && c.contains("page_info") &&
      !field(page, "has_next_page").is_boolean())
    throw std::runtime_error(
        "Meta returned invalid pagination. Previous library was kept.");
  s.complete = field(page, "has_next_page") != true;
  if (!s.complete)
    s.warning = "Meta returned a partial library. Previously confirmed titles "
                "were kept.";
  auto &user = field(field(field(response, "data"), "viewer"), "user");
  s.account_id = str(user, "id");
  if (!s.account_id.empty() && !digits(s.account_id))
    throw std::runtime_error("Meta returned an invalid account identity.");
  s.alias = str(user, "alias");
  s.display_name = str(user, "display_name");
  if (s.alias.empty())
    s.alias = s.display_name;
  return s;
}
uint64_t Plan::size() const {
  uint64_t s = 0;
  for (auto &f : files) {
    if (f.size > UINT64_MAX - s)
      throw std::runtime_error("Invalid build size.");
    s += f.size;
  }
  return s;
}
Plan parse_manifest(std::string_view app_id, std::string_view binary_id, const Json &m) {
  Plan p;
  p.app_id = app_id;
  p.binary_id = binary_id;
  p.version = str(m, "version");
  p.version_code = str(m, "versionCode");
  p.canonical = str(m, "canonicalName");
  p.launch_file = str(m, "launchFile");
  p.launch_parameters = str(m, "launchParameters");
  const auto &files = field(m, "files");
  if (!files.is_object())
    throw std::runtime_error("The build manifest contains no file list.");
  std::set<std::string> names;
  for (const auto &[name, item] : files.items()) {
    File f;
    f.name = safe_relative(name);
    auto key = f.name;
    std::ranges::transform(key, key.begin(), [](unsigned char c) { return char(std::tolower(c)); });
    if (!names.insert(key).second)
      throw std::runtime_error("The build manifest lists " + f.name + " twice.");
    f.sha256 = str(item, "sha256");
    const auto &size = field(item, "size");
    if (!sha256_hex(f.sha256) || !size.is_number_unsigned())
      throw std::runtime_error("The build manifest has invalid details for " + f.name + ".");
    f.size = size.get<uint64_t>();
    const auto &segments = field(item, "segments");
    if (!segments.is_array())
      throw std::runtime_error("The build manifest has no segments for " + f.name + ".");
    for (const auto &segment : segments) {
      // [offset, sha256 of the inflated bytes, compressed size]
      if (!segment.is_array() || segment.size() < 2 || !segment[1].is_string() ||
          !sha256_hex(segment[1].get<std::string>()))
        throw std::runtime_error("The build manifest has an invalid segment for " + f.name + ".");
      File::Segment s{segment[1].get<std::string>(), 0};
      if (segment.size() > 2 && segment[2].is_number_unsigned())
        s.compressed = segment[2].get<uint64_t>();
      f.segments.push_back(std::move(s));
    }
    if (f.size && f.segments.empty())
      throw std::runtime_error("The build manifest has no segments for " + f.name + ".");
    p.files.push_back(std::move(f));
  }
  return p;
}
std::string app_id_from_link(std::string_view link) {
  // meta.com/experiences/pcvr/<id>, oculus.com/experiences/rift/<id>, or the
  // id: the last run of digits long enough to be an app id.
  std::string id;
  for (std::size_t i = 0; i < link.size();) {
    auto end = i;
    while (end < link.size() && link[end] >= '0' && link[end] <= '9')
      ++end;
    if (end - i >= 8)
      id = link.substr(i, end - i);
    i = std::max(end, i + 1);
  }
  return id;
}
Json Store::query(std::string_view doc, const Json &vars) const {
  if (token_.empty())
    throw std::runtime_error("Not signed in to Meta.");
  return request("https://graph.oculus.com/graphql",
                 {{"access_token", token_},
                  {"doc_id", std::string(doc)},
                  {"variables", vars.dump()}});
}
Json Store::request(std::string_view url, const http::Fields &fields) const {
  return transport_ ? transport_(url, fields) : post(url, fields, stop_);
}
http::Response Store::fetch(std::string_view url, size_t limit) const {
  return read_ ? read_(url, {}) : http::get_delivery(url, limit, stop_);
}
Snapshot Store::identity() const {
  if (token_.empty())
    throw std::runtime_error("Not signed in to Meta.");
  auto j = request("https://graph.oculus.com/me", {{"access_token", token_},
                                                   {"fields", "id,alias,display_name"},
                                                   {"method", "GET"}});
  Snapshot s;
  s.account_id = str(j, "id");
  if (!digits(s.account_id))
    throw std::runtime_error("Meta returned an invalid account identity.");
  s.alias = str(j, "alias");
  s.display_name = str(j, "display_name");
  if (s.alias.empty())
    s.alias = s.display_name;
  return s;
}
Snapshot Store::library() const {
  // The whole library is one reply (the query has no paging); a one-off 5xx
  // is retried before it counts.
  for (int attempt = 0;; ++attempt) {
    try {
      return parse_library(query(kLibraryDoc));
    } catch (const RequestError &error) {
      if (error.status < 500 || error.status >= 600 || stop_.stop_requested()) throw;
      log::warn("meta", "Library query attempt {} failed: {}", attempt + 1, error.what());
      if (attempt == 2 || !pause_for(stop_, std::chrono::milliseconds(500 << attempt))) {
        std::string reason = error.what();
        if (auto end = reason.find(". Try signing in again."); end != reason.npos)
          reason.resize(end);
        throw RequestError(error.status, "Meta could not list your games right now (" + reason +
                                             "). Use Refresh in the library to try again.");
      }
    }
  }
}
std::vector<Build> Store::builds(std::string_view id, std::string *name) const {
  require_id(id);
  Json response;
  for (int attempt = 0;; ++attempt) {
    try {
      response = query(kBuildsDoc, {{"id", id}});
      break;
    } catch (const RequestError &error) {
      if (error.status < 500 || error.status >= 600 || attempt == 2 ||
          !pause_for(stop_, std::chrono::milliseconds(1000 << attempt)))
        throw;
    }
  }
  const auto &node = field(field(response, "data"), "node");
  if (!node.is_object())
    throw std::runtime_error("Meta returned no Rift application for that app ID.");
  if (name) {
    *name = str(node, "displayName");
    if (name->empty())
      *name = str(node, "display_name");
  }
  std::vector<Build> out;
  for (const auto &binary : nodes(field(node, "supportedBinaries"))) {
    if (str(binary, "__typename") != "PCBinary")
      continue;
    Build b{str(binary, "id"), str(binary, "version"), str(binary, "versionCode"),
            str(binary, "changeLog")};
    if (!digits(b.binary_id))
      continue;
    if (b.version.empty())
      b.version = "unknown";
    out.push_back(std::move(b));
  }
  if (out.empty())
    throw std::runtime_error("Meta has no Rift (PC VR) build of this app for this account.");
  std::ranges::stable_sort(out, std::greater<>{}, [](const Build &b) { return number(Json(b.version_code)); });
  return out;
}
Json Store::manifest(std::string_view binary_id) const {
  if (!digits(binary_id))
    throw std::runtime_error("Invalid Meta build ID.");
  if (token_.empty())
    throw std::runtime_error("Not signed in to Meta.");
  http::Response response;
  for (int attempt = 0;; ++attempt) {
    response = fetch("https://securecdn.oculus.com/binaries/download/?" +
                         http::form({{"id", std::string(binary_id)},
                                     {"access_token", token_},
                                     {"get_manifest", "1"}}),
                     256 * 1024 * 1024);
    if (response.status == 401 || response.status == 403)
      throw RequestError(response.status, "Meta refused the build manifest. Confirm this account owns the Rift "
                                          "version, then sign in again.");
    bool retry = response.status == 429 || response.status >= 500;
    if (!retry || attempt == 2 || !pause_for(stop_, std::chrono::milliseconds(1000 << attempt)))
      break;
  }
  if (response.status < 200 || response.status >= 300)
    throw RequestError(response.status, std::format("The build manifest request returned HTTP {}.", response.status));
  mz_zip_archive zip{};
  if (!mz_zip_reader_init_mem(&zip, response.body.data(), response.body.size(), 0))
    throw std::runtime_error("Meta returned an invalid build manifest.");
  struct Close {
    mz_zip_archive *zip;
    ~Close() { mz_zip_reader_end(zip); }
  } close{&zip};
  int index = mz_zip_reader_locate_file(&zip, "manifest.json", nullptr, 0);
  if (index < 0 && mz_zip_reader_get_num_files(&zip) > 0)
    index = 0;
  size_t size = 0;
  void *bytes = index < 0 ? nullptr : mz_zip_reader_extract_to_heap(&zip, mz_uint(index), &size, 0);
  if (!bytes)
    throw std::runtime_error("Meta returned an invalid build manifest.");
  std::string text(static_cast<const char *>(bytes), size);
  mz_free(bytes);
  auto json = parse_json(text);
  if (!field(json, "files").is_object())
    throw std::runtime_error("The build manifest contains no file list.");
  return json;
}
std::string Store::segment(std::string_view binary_id, std::string_view sha256) const {
  if (!digits(binary_id) || !sha256_hex(sha256))
    throw std::runtime_error("Invalid segment request.");
  auto response = fetch("https://securecdn.oculus.com/binaries/segment/?" +
                            http::form({{"access_token", token_},
                                        {"binary_id", std::string(binary_id)},
                                        {"segment_sha256", std::string(sha256)}}),
                        64 * 1024 * 1024);
  if (response.status < 200 || response.status >= 300)
    throw RequestError(response.status, std::format("Segment {} returned HTTP {}.", sha256.substr(0, 12), response.status));
  return std::move(response.body);
}
Plan Store::owned_plan(std::string_view id, std::string_view binary_id) const {
  std::string name;
  auto list = builds(id, &name);
  auto build = list.front();
  if (!binary_id.empty()) {
    auto found = std::ranges::find(list, binary_id, &Build::binary_id);
    if (found == list.end())
      throw std::runtime_error("Meta no longer offers this build of the game.");
    build = *found;
  }
  auto plan = parse_manifest(id, build.binary_id, manifest(build.binary_id));
  plan.name = name.empty() ? plan.canonical : name;
  plan.version = build.version;
  plan.version_code = build.version_code.empty() ? plan.version_code : build.version_code;
  if (plan.version_code.empty())
    plan.version_code = "0";
  plan.package = rift::slugify(plan.name);
  return plan;
}
Json Store::public_item(std::string_view id) const {
  require_id(id);
  if (listing_)
    return listing_(id);
  // The public storefront requires its anonymous CSRF verifier in both the
  // form and X-FB-LSD header. This is fetched without account credentials;
  // the account's access token is never sent to www.meta.com.
  thread_local std::string verifier;
  Json j;
  for (int attempt = 0; attempt < 2; ++attempt) {
    if (verifier.empty()) {
      auto html = http::get_public(
          "https://www.meta.com/experiences/" + std::string(id) + "/", stop_);
      std::smatch match;
      const std::regex pattern(
          R"riftlift(\["LSD",\[\],\{"token":"([A-Za-z0-9_-]{1,200})")riftlift");
      if (!std::regex_search(html, match, pattern))
        throw std::runtime_error(
            "Public Meta artwork is unavailable. Refresh to retry.");
      verifier = match[1].str();
    }
    try {
      j = post(
          "https://www.meta.com/ocapi/graphql",
          {{"doc_id", "6549406941839522"},
           {"lsd", verifier},
           {"variables", Json{{"itemId", id}, {"hmdType", "RIFT"}}.dump()}},
          stop_, {{"X-FB-LSD", verifier}});
      break;
    } catch (...) {
      verifier.clear();
      if (attempt || stop_.stop_requested())
        throw;
    }
  }
  auto &item = field(field(j, "data"), "item");
  if (str(item, "id") != id)
    throw std::runtime_error("Artwork listing is unavailable.");
  return item;
}
Game Store::artwork(std::string_view id) const { return card(public_item(id)); }
Details Store::details(std::string_view id) const {
  require_id(id);
  Details result;
  try {
    result.game = artwork(id);
  } catch (...) {
    if (stop_.stop_requested() || token_.empty())
      throw;
    result.game.id = id;
  }
  if (!token_.empty()) {
    std::string name;
    auto list = builds(id, &name);
    if (result.game.name.empty() || result.game.name.starts_with("Rift app "))
      result.game.name = name;
    result.live_id = list.front().binary_id;
    result.version = list.front().version;
    result.version_code = list.front().version_code;
  }
  return result;
}
std::string cover_from_page(std::string_view html) {
  // JSON-LD: "primaryImageOfPage":{"@id":"https:\/\/..."}. Scanned by hand:
  // store pages are large and std::regex recursion is unsafe on them.
  if (auto at = html.find("\"primaryImageOfPage\""); at != html.npos) {
    auto id = html.find("\"@id\"", at);
    auto open = id == html.npos ? id : html.find('"', html.find(':', id + 5));
    if (open != html.npos && id - at < 64) {
      size_t close = open + 1;
      while (close < html.size() && html[close] != '"')
        close += html[close] == '\\' ? 2 : 1;
      try {
        auto url = Json::parse(html.substr(open, close - open + 1)).get<std::string>();
        if (http::allowed_delivery(url))
          return url;
      } catch (...) {
      }
    }
  }
  if (auto at = html.find("property=\"og:image\""); at != html.npos) {
    auto tag = html.rfind('<', at), end = html.find('>', at);
    auto content = html.find("content=\"", tag);
    if (tag != html.npos && content != html.npos && content < end) {
      content += 9;
      std::string url(html.substr(content, html.find('"', content) - content));
      for (size_t amp; (amp = url.find("&amp;")) != std::string::npos;)
        url.replace(amp, 5, "&");
      if (http::allowed_delivery(url))
        return url;
    }
  }
  return {};
}
std::string Store::cover(std::string_view id) const {
  require_id(id);
  return cover_from_page(http::get_public(
      "https://www.meta.com/experiences/" + std::string(id) + "/", stop_));
}
Snapshot Store::owned_listing(std::string_view id) const {
  require_id(id);
  // Cross-buy purchases are Quest entitlements, so the PC library leaves them
  // out; Meta listing a Rift build to this account is what lets it download.
  std::string name;
  builds(id, &name);
  Snapshot s;
  s.account_id = identity().account_id;
  Game g;
  try {
    g = artwork(id);
  } catch (...) {
    if (stop_.stop_requested())
      throw;
    g.id = id;
  }
  if (g.name.empty() || g.name.starts_with("Rift app "))
    g.name = name.empty() ? "Rift app " + std::string(id) : name;
  if (g.package.empty() || !name.empty())
    g.package = rift::slugify(name.empty() ? g.name : name);
  g.platform = "PC";
  g.owned = true;
  s.games.push_back(std::move(g));
  return s;
}
bool Store::owns(std::string_view id) const {
  const auto entitlement = library();
  return std::ranges::find(entitlement.games, std::string(id), &Game::id) != entitlement.games.end();
}
} // namespace riftlift::meta
