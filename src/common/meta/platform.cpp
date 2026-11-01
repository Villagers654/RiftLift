#include "common/meta/platform.h"
#include "common/log.h"
#include "common/meta/store.h"
#include <algorithm>
#include <condition_variable>
#include <thread>

namespace riftlift::meta {
namespace {
constexpr std::string_view kGraph = "https://graph.oculus.com";
Json failure(PlatformError error) { return Json{{"error", int(error)}}; }
// What a failed Meta request means for the game. Never carries server text.
PlatformError classify() {
  try {
    throw;
  } catch (const RequestError &e) {
    const std::string_view text = e.what();
    if (e.status == 401 || e.status == 403 || text.find(" code 190") != text.npos)
      return PlatformError::unauthenticated;
    return PlatformError::unavailable;
  } catch (const Json::exception &) {
    return PlatformError::invalid_response;
  } catch (...) {
    return PlatformError::unavailable;
  }
}
bool transient() {
  try {
    throw;
  } catch (const RequestError &e) {
    return e.status == 429 || (e.status >= 500 && e.status < 600);
  } catch (const Json::exception &) {
    return false;
  } catch (...) {
    return true;  // no reply at all: a dropped connection
  }
}
bool pause_for(std::stop_token stop, std::chrono::milliseconds delay) {
  std::mutex mutex;
  std::condition_variable_any wake;
  std::unique_lock lock(mutex);
  wake.wait_for(lock, stop, delay, [] { return false; });
  return !stop.stop_requested();
}
} // namespace

std::string platform_error_text(PlatformError error) {
  switch (error) {
  case PlatformError::none: return {};
  case PlatformError::unsupported: return "Not supported on this Platform.";
  case PlatformError::unauthenticated: return "RiftLift: sign in to Meta again";
  case PlatformError::not_entitled: return "RiftLift: this Meta account does not own this app";
  case PlatformError::unavailable: return "RiftLift: Meta service or network unavailable";
  case PlatformError::invalid_response: return "RiftLift: invalid Meta service response";
  case PlatformError::identity_mismatch: return "RiftLift: app or account session identity changed or mismatched";
  case PlatformError::cancelled: return "RiftLift: Platform request cancelled";
  case PlatformError::uninitialized: return "RiftLift: Platform is not initialized";
  case PlatformError::incomplete: return "RiftLift: incomplete entitlement response; ownership unknown";
  case PlatformError::invalid_argument: return "RiftLift: invalid Platform argument";
  }
  return "RiftLift: Platform service unavailable";
}

PlatformBroker::PlatformBroker(std::string app_id, Load load, Post post, std::stop_token stop)
    : app_id_(std::move(app_id)), load_(std::move(load)), post_(std::move(post)), stop_(stop) {}

Json PlatformBroker::send(std::string_view url, const http::Fields &fields, bool read) {
  // Reads are retried like Venture's: a game quits when its entitlement or
  // identity read fails, and Meta's edge drops reads for a second or two.
  for (int attempt = 0;; ++attempt) {
    try {
      return post_ ? post_(url, fields) : post(url, fields, stop_);
    } catch (...) {
      if (!read || attempt == 4 || !transient() ||
          !pause_for(stop_, std::chrono::milliseconds(250 << attempt)))
        throw;
    }
  }
}

PlatformError PlatformBroker::account(Session &session) {
  std::optional<Session> current;
  try {
    current = load_();
  } catch (...) {
    return PlatformError::unavailable;  // vault errors may hold secrets: never logged
  }
  if (!loaded_) {
    account_ = current;
    loaded_ = true;
  }
  // Without an account the game still runs; its own calls get this answer.
  if (!account_ || account_->access_token.empty())
    return PlatformError::unauthenticated;
  // The account the game started with must still be the one signed in.
  if (!current || current->access_token != account_->access_token)
    return PlatformError::identity_mismatch;
  session = *account_;
  return PlatformError::none;
}

PlatformError PlatformBroker::authenticate(const Session &session) {
  if (!app_token_.empty())
    return PlatformError::none;
  if (identity_error_ != PlatformError::none)
    return identity_error_;
  if (!digits(app_id_, 5, 20))
    return PlatformError::identity_mismatch;
  try {
    // The app-scoped token the Platform SDK would hold: issued for this app id
    // and never the account's own token.
    auto j = send(std::string(kGraph) + "/authenticate_application",
                  {{"app_id", app_id_}, {"access_token", session.access_token}}, true);
    auto token = str(j, "access_token");
    if (token.empty())
      return PlatformError::invalid_response;
    if (token == session.access_token)
      return PlatformError::identity_mismatch;
    if (auto returned = str(j, "app_id"); !returned.empty() && returned != app_id_)
      return PlatformError::identity_mismatch;
    auto me = send(std::string(kGraph) + "/me",
                   {{"access_token", token}, {"fields", "id,alias,display_name"}, {"method", "GET"}}, true);
    auto id = str(me, "id");
    if (!digits(id, 1, 20) || !number(Json(id))) {
      identity_error_ = PlatformError::invalid_response;
      return identity_error_;
    }
    app_token_ = std::move(token);
    user_id_ = std::move(id);
    alias_ = str(me, "alias");
    display_name_ = str(me, "display_name");
    log::info("platform", "Meta issued the app identity for {}.", app_id_);
    return PlatformError::none;
  } catch (...) {
    auto error = classify();
    if (error == PlatformError::unauthenticated)
      identity_error_ = error;
    log::warn("platform", "Meta did not issue the app identity for {}: {}.", app_id_,
              platform_error_text(error));
    return error;
  }
}

PlatformError PlatformBroker::entitlement(const Session &session) {
  if (!digits(app_id_, 5, 20))
    return PlatformError::identity_mismatch;
  // Live, as the game asked: no cached library and no inference from the files
  // on disk.
  Store store(session.access_token, stop_, post_);
  try {
    auto library = store.library();
    if (std::ranges::find(library.games, app_id_, &Game::id) != library.games.end())
      return PlatformError::none;
  } catch (...) {
    return classify();
  }
  // Cross-buy purchases are Quest entitlements the Rift library leaves out:
  // Meta listing this app's Rift build to the account is what grants it.
  try {
    store.builds(app_id_);
    return PlatformError::none;
  } catch (const NotListed &) {
    return PlatformError::not_entitled;
  } catch (...) {
    return classify();
  }
}

Json PlatformBroker::handle(const Json &request) {
  std::lock_guard lock(mutex_);
  if (stop_.stop_requested())
    return failure(PlatformError::cancelled);
  const auto op = str(request, "op");
  // The game declared its own app id when it initialized; a local game may
  // have none in its record. Meta's answers are for that id.
  if (request.contains("appId")) {
    auto id = str(request, "appId");
    if (!digits(id, 5, 20))
      return failure(PlatformError::invalid_argument);
    if (id != app_id_) {
      app_id_ = std::move(id);
      app_token_.clear();
      user_id_.clear();
      identity_error_ = PlatformError::none;
    }
  }
  Session session;
  if (auto error = account(session); error != PlatformError::none)
    return failure(error);
  if (op == "entitlement") {
    auto error = entitlement(session);
    log::info("platform", "Entitlement check for {}: {}.", app_id_,
              error == PlatformError::none ? "entitled" : platform_error_text(error));
    return failure(error);
  }
  if (op != "credentials" && op != "user_proof" && op != "org_scoped_id")
    return failure(PlatformError::unsupported);
  if (auto error = authenticate(session); error != PlatformError::none)
    return failure(error);
  if (op == "credentials")
    return Json{{"error", 0},
                {"userId", user_id_},
                {"alias", alias_},
                {"displayName", display_name_},
                {"appToken", app_token_}};
  try {
    if (op == "user_proof") {
      // Nonces are single use: a write, so never retried.
      auto j = send(std::string(kGraph) + "/user_nonce_generate", {{"access_token", app_token_}}, false);
      auto nonce = str(j, "nonce");
      if (nonce.empty() || nonce.find('\0') != nonce.npos)
        return failure(PlatformError::invalid_response);
      return Json{{"error", 0}, {"nonce", nonce}};
    }
    auto user = str(request, "userId");
    if (!digits(user, 1, 20) || !number(Json(user)))
      return failure(PlatformError::invalid_argument);
    auto j = send(std::string(kGraph) + "/" + user,
                  {{"access_token", app_token_}, {"fields", "org_scoped_id"}, {"method", "GET"}}, true);
    auto id = str(j, "org_scoped_id");
    if (!digits(id, 1, 20) || !number(Json(id)))
      return failure(PlatformError::invalid_response);
    return Json{{"error", 0}, {"orgScopedId", id}};
  } catch (...) {
    auto error = classify();
    // An expired app token is exchanged again on the next request.
    if (error == PlatformError::unauthenticated)
      app_token_.clear();
    return failure(error);
  }
}
} // namespace riftlift::meta
