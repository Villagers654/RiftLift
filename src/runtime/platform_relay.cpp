#include "runtime/platform_relay.h"
#include "common/log.h"
#include <array>
#include <format>
#include <random>
#include <stdexcept>
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

namespace riftlift::runtime {
namespace {
constexpr int kUnavailable = -20003, kInvalidArgument = -20009, kUnsupported = -20000;
constexpr std::size_t kLimit = 64 * 1024;
#ifdef _WIN32
using Socket = SOCKET;
constexpr Socket kNone = INVALID_SOCKET;
void close_socket(Socket s) { closesocket(s); }
#else
using Socket = int;
constexpr Socket kNone = -1;
void close_socket(Socket s) { ::close(s); }
#endif
std::string random_secret() {
  std::random_device device;
  std::string out;
  for (int i = 0; i < 8; ++i)
    out += std::format("{:08x}", std::uint32_t(device()));
  return out;
}
// The inherited pipe must not reach Proton or the game.
void keep_private(PipeChannel &channel) {
  for (auto handle : channel.handles()) {
#ifdef _WIN32
    SetHandleInformation(reinterpret_cast<HANDLE>(handle), HANDLE_FLAG_INHERIT, 0);
#else
    fcntl(int(handle), F_SETFD, FD_CLOEXEC);
#endif
  }
}
bool same(std::string_view a, std::string_view b) {
  unsigned difference = a.size() != b.size();
  for (size_t i = 0; i < a.size() && i < b.size(); ++i)
    difference |= unsigned(a[i] ^ b[i]);
  return !difference;
}
} // namespace

Json PlatformRelay::forward(const Json &request, std::string_view secret,
                            const std::function<Json(const Json &)> &call) {
  if (!request.is_object() || !same(str(request, "secret"), secret))
    return Json{{"error", kInvalidArgument}};
  const auto op = str(request, "op");
  if (op != "credentials" && op != "entitlement" && op != "user_proof" && op != "org_scoped_id")
    return Json{{"error", kUnsupported}};
  Json out{{"op", op}};
  for (auto key : {"appId", "userId"})
    if (request.contains(key)) {
      auto value = str(request, key);
      if (!digits(value, 1, 20))
        return Json{{"error", kInvalidArgument}};
      out[key] = value;
    }
  try {
    auto reply = call(out);
    // The launcher's own failure reply is text: the game gets a code.
    if (!reply.is_object() || !field(reply, "error").is_number_integer())
      return Json{{"error", kUnavailable}};
    return reply;
  } catch (...) {
    return Json{{"error", kUnavailable}};
  }
}

PlatformRelay::PlatformRelay(std::shared_ptr<PipeChannel> launcher, std::string app_id)
    : launcher_(std::move(launcher)), secret_(random_secret()) {
  keep_private(*launcher_);
#ifdef _WIN32
  WSADATA data;
  if (WSAStartup(MAKEWORD(2, 2), &data) != 0)
    throw std::runtime_error("could not start the Platform relay (Winsock)");
  Socket s = WSASocketW(AF_INET, SOCK_STREAM, IPPROTO_TCP, nullptr, 0, WSA_FLAG_NO_HANDLE_INHERIT);
#else
  Socket s = ::socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
#endif
  if (s == kNone)
    throw std::runtime_error("could not open the Platform relay socket");
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  socklen_t length = sizeof(address);
  if (::bind(s, reinterpret_cast<sockaddr *>(&address), sizeof(address)) != 0 || ::listen(s, 8) != 0 ||
      ::getsockname(s, reinterpret_cast<sockaddr *>(&address), &length) != 0) {
    close_socket(s);
    throw std::runtime_error("could not open the Platform relay on the loopback address");
  }
  socket_ = std::uintptr_t(s);
  port_ = ntohs(address.sin_port);
  if (digits(app_id, 5, 20)) {
    early_ = Early::pending;
    early_app_ = app_id;
    early_thread_ = std::jthread([this, request = Json{{"op", "entitlement"}, {"appId", app_id}}] {
      Json reply;
      try {
        reply = launcher_->call(request);
      } catch (...) {
      }
      {
        std::lock_guard lock(early_mutex_);
        early_reply_ = std::move(reply);
        early_ = Early::ready;
      }
      early_ready_.notify_all();
    });
  }
  thread_ = std::jthread([this](std::stop_token stop) { serve(stop); });
}

PlatformRelay::~PlatformRelay() {
  thread_.request_stop();
  if (thread_.joinable())
    thread_.join();
  clients_.clear();  // joins each
  if (early_thread_.joinable())
    early_thread_.join();
  close_socket(Socket(socket_));
#ifdef _WIN32
  WSACleanup();
#endif
}

std::string PlatformRelay::endpoint() const { return std::format("127.0.0.1:{} {}", port_, secret_); }

void PlatformRelay::serve(std::stop_token stop) {
  const auto listener = Socket(socket_);
  while (!stop.stop_requested()) {
#ifdef _WIN32
    fd_set ready;
    FD_ZERO(&ready);
    FD_SET(listener, &ready);
    timeval wait{0, 200 * 1000};
    if (select(0, &ready, nullptr, nullptr, &wait) <= 0)
      continue;
    Socket client = accept(listener, nullptr, nullptr);
    DWORD timeout = 5000;
#else
    pollfd ready{listener, POLLIN, 0};
    if (::poll(&ready, 1, 200) <= 0)
      continue;
    Socket client = ::accept4(listener, nullptr, nullptr, SOCK_CLOEXEC);
    timeval timeout{5, 0};
#endif
    if (client == kNone)
      continue;
    setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char *>(&timeout), sizeof(timeout));
    std::erase_if(clients_, [](const Client &c) { return c.done->load(); });
    auto done = std::make_shared<std::atomic<bool>>(false);
    clients_.push_back({std::jthread([this, client, done] {
                          answer_client(std::uintptr_t(client));
                          *done = true;
                        }),
                        done});
  }
}

// The launcher's answer, or for the game's first entitlement check the one
// asked at launch, waited for if Meta has not answered it yet. Only an
// "entitled" is kept: anything else is asked again now.
Json PlatformRelay::ask(const Json &request) {
  if (str(request, "op") == "entitlement" && (!request.contains("appId") || str(request, "appId") == early_app_)) {
    std::unique_lock lock(early_mutex_);
    early_ready_.wait(lock, [&] { return early_ != Early::pending; });
    if (early_ == Early::ready) {
      early_ = Early::taken;
      if (field(early_reply_, "error") == 0) {
        log::info("platform", "The game's entitlement check got the answer asked at launch.");
        return early_reply_;
      }
    }
  }
  return launcher_->call(request);
}

void PlatformRelay::answer_client(std::uintptr_t handle) {
  const auto client = Socket(handle);
  // One request per connection: a JSON object ending in a newline.
  std::string line;
  std::array<char, 4096> buffer;
  while (line.find('\n') == line.npos && line.size() < kLimit) {
    auto n = ::recv(client, buffer.data(), int(buffer.size()), 0);
    if (n <= 0)
      break;
    line.append(buffer.data(), size_t(n));
  }
  if (auto end = line.find('\n'); end != line.npos) {
    line.resize(end);
    auto request = Json::parse(line, nullptr, false);
    auto reply = forward(request, secret_, [&](const Json &r) { return ask(r); });
    // The reply's codes only: it can hold the app token.
    log::info("platform", "Game asked for {}: error {}.", str(request, "op"), field(reply, "error").dump());
    auto text = reply.dump() + "\n";
    for (size_t sent = 0; sent < text.size();) {
      auto n = ::send(client, text.data() + sent, int(text.size() - sent), 0);
      if (n <= 0)
        break;
      sent += size_t(n);
    }
  }
  close_socket(client);
}

} // namespace riftlift::runtime
