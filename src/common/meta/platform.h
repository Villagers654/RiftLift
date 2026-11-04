#pragma once
// The Oculus Platform answers a running game gets from Meta: its app-scoped
// identity, its entitlement and user proofs. The launcher holds the account's
// token; the game reaches this broker through riftlift-runtime's relay
// (runtime/platform_relay.h) and never sees that token.
#include "common/meta/auth.h"
#include <functional>
#include <mutex>
#include <optional>

namespace riftlift::meta {
// The Platform SDK's errors are service codes, not errno. RiftLift keeps
// Venture's reserved range so games get a stable error that is never mistaken
// for one of Meta's.
enum class PlatformError : int {
  none = 0,
  unsupported = -20000,
  unauthenticated = -20001,
  not_entitled = -20002,
  unavailable = -20003,
  invalid_response = -20004,
  identity_mismatch = -20005,
  cancelled = -20006,
  uninitialized = -20007,
  incomplete = -20008,
  invalid_argument = -20009,
};
// The text a game shows for `error` (Error_GetMessage).
std::string platform_error_text(PlatformError error);

class PlatformBroker {
public:
  // This account's sign-in, read again on each request: nothing when there is
  // none (a launch without an account, or one signed out since).
  using Load = std::function<std::optional<Session>()>;
  // meta::post; tests replace it.
  using Post = std::function<Json(std::string_view url, const http::Fields &)>;
  PlatformBroker(std::string app_id, Load load, Post post = {}, std::stop_token stop = {});
  // One request from the game: {"op", "appId"?, "userId"?} ->
  // {"error", ...}. Thread-safe. Meta is asked only here, when the game asks:
  //   credentials   -> userId, alias, displayName, appToken
  //   entitlement   -> error only; a live query, never cached (the runtime
  //                    asks once at launch, for the game's first check)
  //   user_proof    -> nonce (single use, never retried)
  //   org_scoped_id -> orgScopedId of userId
  Json handle(const Json &request);

private:
  std::string app_id_;
  Load load_;
  Post post_;
  std::stop_token stop_;
  std::mutex mutex_;
  std::optional<Session> account_;  // as at the first request
  bool loaded_ = false;
  // The app-scoped identity, once Meta has issued it.
  std::string app_token_, user_id_, alias_, display_name_;
  PlatformError identity_error_ = PlatformError::none;  // a lasting refusal
  PlatformError account(Session &session);
  PlatformError authenticate(const Session &session);
  PlatformError entitlement(const Session &session);
  Json send(std::string_view url, const http::Fields &fields, bool read);
};
} // namespace riftlift::meta
