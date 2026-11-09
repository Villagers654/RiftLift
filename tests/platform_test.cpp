#include "common/meta/platform.h"
#include <doctest/doctest.h>

using namespace riftlift;
using namespace riftlift::meta;

namespace {
// Meta, as the broker sees it: each request is recorded and answered here.
struct FakeMeta {
  std::vector<std::string> urls;
  std::vector<http::Fields> fields;
  std::vector<std::string> owned{"1368187813209608"};
  bool cross_buy = false;  // the Rift build is listed though the library lacks it
  long refuse = 0;         // HTTP status every request fails with
  long refuse_builds = 0;  // HTTP status the build listing fails with
  int drops = 0;           // connections dropped before an answer
  Json answer(std::string_view url, const http::Fields &f) {
    urls.emplace_back(url);
    fields.push_back(f);
    if (drops > 0) {
      --drops;
      throw std::runtime_error("Could not connect.");
    }
    if (refuse)
      throw RequestError(refuse, "Meta graphql failed (HTTP " + std::to_string(refuse) + " code 190).");
    auto get = [&](std::string_view key) {
      for (auto &[k, v] : f)
        if (k == key)
          return v;
      return std::string();
    };
    if (url.ends_with("/authenticate_application"))
      return Json{{"access_token", "app-token-" + get("app_id")}, {"app_id", get("app_id")}};
    if (url.ends_with("/me"))
      return Json{{"id", "4321"}, {"alias", "tester"}, {"display_name", "Test User"}};
    if (url.ends_with("/user_nonce_generate"))
      return Json{{"nonce", "nonce-1"}};
    if (url.ends_with("/4321"))
      return Json{{"org_scoped_id", "999"}, {"id", "4321"}};
    if (get("doc_id") == "4850747515044496") {
      Json nodes = Json::array();
      for (auto &id : owned)
        nodes.push_back({{"item", {{"id", id}, {"platform", "PC"}, {"display_name", "Lone Echo"}}}});
      return Json{{"data", {{"viewer", {{"user", {{"active_entitlements", {{"nodes", nodes}}}}}}}}}};
    }
    if (get("doc_id") == "1586217024733717") {
      Json binaries = Json::array();
      const auto app = str(Json::parse(get("variables")), "id");
      if (refuse_builds)
        throw RequestError(refuse_builds, "Meta graphql failed (HTTP " + std::to_string(refuse_builds) + " code 1).");
      if (cross_buy || std::ranges::find(owned, app) != owned.end())
        binaries.push_back({{"__typename", "PCBinary"}, {"id", "555"}, {"version", "1.0"}, {"versionCode", "1"}});
      return Json{{"data", {{"node", {{"displayName", "Game"}, {"supportedBinaries", {{"nodes", binaries}}}}}}}};
    }
    throw RequestError(404, "Meta request failed (HTTP 404).");
  }
};
Session account(std::string token = "profile-token") {
  Session s;
  s.access_token = std::move(token);
  s.account_id = "1001";
  return s;
}
int error(const Json &reply) { return field(reply, "error").get<int>(); }
} // namespace

TEST_CASE("platform broker: nothing is asked until the game asks") {
  FakeMeta meta;
  std::optional<Session> signed_in = account();
  PlatformBroker broker("1368187813209608", [&] { return signed_in; },
                        [&](std::string_view u, const http::Fields &f) { return meta.answer(u, f); });
  CHECK(meta.urls.empty());

  // The identity is the app-scoped one Meta issues, never the account's token.
  auto credentials = broker.handle({{"op", "credentials"}});
  CHECK(error(credentials) == 0);
  CHECK(credentials["userId"] == "4321");
  CHECK(credentials["alias"] == "tester");
  CHECK(credentials["displayName"] == "Test User");
  CHECK(credentials["appToken"] == "app-token-1368187813209608");
  CHECK(meta.urls.size() == 2);
  CHECK(broker.handle({{"op", "credentials"}})["appToken"] == "app-token-1368187813209608");
  CHECK(meta.urls.size() == 2);  // issued once

  // Entitlement: a live query every time the game asks, never cached.
  CHECK(error(broker.handle({{"op", "entitlement"}})) == 0);
  CHECK(meta.urls.size() == 3);
  meta.owned.clear();
  CHECK(error(broker.handle({{"op", "entitlement"}})) == int(PlatformError::not_entitled));
  // Cross-buy: Meta lists the Rift build to the account.
  meta.cross_buy = true;
  CHECK(error(broker.handle({{"op", "entitlement"}})) == 0);

  auto proof = broker.handle({{"op", "user_proof"}});
  CHECK(proof["nonce"] == "nonce-1");
  auto org = broker.handle({{"op", "org_scoped_id"}, {"userId", "4321"}});
  CHECK(org["orgScopedId"] == "999");
  CHECK(error(broker.handle({{"op", "org_scoped_id"}, {"userId", "x"}})) == int(PlatformError::invalid_argument));
  CHECK(error(broker.handle({{"op", "purchase"}})) == int(PlatformError::unsupported));
}

TEST_CASE("platform broker: an owned game's entitlement is one small query, not the library") {
  FakeMeta meta;
  std::optional<Session> signed_in = account();
  PlatformBroker broker("1368187813209608", [&] { return signed_in; },
                        [&](std::string_view u, const http::Fields &f) { return meta.answer(u, f); });
  auto library_asked = [&] {
    return std::ranges::count_if(meta.fields, [](const http::Fields &f) {
      return std::ranges::find(f, std::pair<std::string, std::string>{"doc_id", "4850747515044496"}) != f.end();
    });
  };
  CHECK(error(broker.handle({{"op", "entitlement"}})) == 0);
  CHECK(meta.urls.size() == 1);
  CHECK(library_asked() == 0);
  // A listing Meta cannot answer: the library decides, and without the game
  // there, ownership is unknown rather than refused.
  meta.refuse_builds = 429;
  CHECK(error(broker.handle({{"op", "entitlement"}})) == 0);
  CHECK(library_asked() == 1);
  meta.owned.clear();
  CHECK(error(broker.handle({{"op", "entitlement"}})) != int(PlatformError::not_entitled));
  CHECK(error(broker.handle({{"op", "entitlement"}})) != 0);
}

TEST_CASE("platform broker: the app the game declared") {
  FakeMeta meta;
  std::optional<Session> signed_in = account();
  // A local game's record names no app id until the game initializes.
  PlatformBroker broker("", [&] { return signed_in; },
                        [&](std::string_view u, const http::Fields &f) { return meta.answer(u, f); });
  CHECK(error(broker.handle({{"op", "entitlement"}})) == int(PlatformError::identity_mismatch));
  CHECK(meta.urls.empty());
  CHECK(error(broker.handle({{"op", "entitlement"}, {"appId", "1368187813209608"}})) == 0);
  CHECK(broker.handle({{"op", "credentials"}})["appToken"] == "app-token-1368187813209608");
  // Another app id: a new app token, issued for it.
  CHECK(broker.handle({{"op", "credentials"}, {"appId", "1234567890"}})["appToken"] == "app-token-1234567890");
  CHECK(error(broker.handle({{"op", "credentials"}, {"appId", "12ab"}})) == int(PlatformError::invalid_argument));
}

TEST_CASE("platform broker: accounts and failures") {
  FakeMeta meta;
  auto post = [&](std::string_view u, const http::Fields &f) { return meta.answer(u, f); };
  // No account: the game still runs, and its requests say so.
  PlatformBroker none("1368187813209608", [] { return std::optional<Session>{}; }, post);
  CHECK(error(none.handle({{"op", "entitlement"}})) == int(PlatformError::unauthenticated));
  CHECK(error(none.handle({{"op", "credentials"}})) == int(PlatformError::unauthenticated));
  CHECK(meta.urls.empty());

  // The account the game started with must stay signed in.
  std::optional<Session> signed_in = account();
  PlatformBroker broker("1368187813209608", [&] { return signed_in; }, post);
  CHECK(error(broker.handle({{"op", "entitlement"}})) == 0);
  signed_in = account("another-token");
  CHECK(error(broker.handle({{"op", "entitlement"}})) == int(PlatformError::identity_mismatch));
  signed_in.reset();
  CHECK(error(broker.handle({{"op", "credentials"}})) == int(PlatformError::identity_mismatch));

  // A revoked sign-in, and dropped connections that are retried.
  std::optional<Session> fresh = account();
  PlatformBroker revoked("1368187813209608", [&] { return fresh; }, post);
  meta.refuse = 400;
  CHECK(error(revoked.handle({{"op", "credentials"}})) == int(PlatformError::unauthenticated));
  meta.refuse = 0;
  meta.drops = 2;
  PlatformBroker flaky("1368187813209608", [&] { return fresh; }, post);
  CHECK(error(flaky.handle({{"op", "credentials"}})) == 0);
  CHECK(meta.drops == 0);
  CHECK(platform_error_text(PlatformError::not_entitled).find("does not own") != std::string::npos);
}
