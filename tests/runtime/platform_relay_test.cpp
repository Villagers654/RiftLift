#include "runtime/platform_relay.h"
#include <doctest/doctest.h>
#include <thread>
#ifdef _WIN32
#include <winsock2.h>
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

using namespace riftlift;
using namespace riftlift::runtime;

TEST_CASE("runtime: the Platform relay passes only the shim's requests") {
  std::vector<Json> asked;
  auto call = [&](const Json &request) {
    asked.push_back(request);
    return Json{{"error", 0}, {"userId", "42"}};
  };
  // The secret must match, and only these fields reach the launcher.
  auto reply = PlatformRelay::forward(
      {{"secret", "abc"}, {"op", "credentials"}, {"appId", "1368187813209608"}, {"extra", "x"}}, "abc", call);
  CHECK(reply["userId"] == "42");
  REQUIRE(asked.size() == 1);
  CHECK(asked[0] == Json{{"op", "credentials"}, {"appId", "1368187813209608"}});
  CHECK(PlatformRelay::forward({{"secret", "abd"}, {"op", "credentials"}}, "abc", call)["error"] == -20009);
  CHECK(PlatformRelay::forward({{"op", "credentials"}}, "abc", call)["error"] == -20009);
  CHECK(PlatformRelay::forward({{"secret", "abc"}, {"op", "refresh"}}, "abc", call)["error"] == -20000);
  CHECK(PlatformRelay::forward({{"secret", "abc"}, {"op", "entitlement"}, {"appId", "1\"}"}}, "abc", call)["error"] ==
        -20009);
  CHECK(asked.size() == 1);
  // A launcher that cannot answer is "unavailable" to the game.
  CHECK(PlatformRelay::forward({{"secret", "abc"}, {"op", "entitlement"}}, "abc",
                               [](const Json &) -> Json { throw std::runtime_error("closed"); })["error"] == -20003);
  CHECK(PlatformRelay::forward({{"secret", "abc"}, {"op", "entitlement"}}, "abc",
                               [](const Json &) { return Json{{"error", "Credential broker unavailable"}}; })["error"] ==
        -20003);
}

TEST_CASE("runtime: the Platform relay answers over loopback") {
  auto [launcher, runtime] = PipeChannel::pair();
  std::jthread broker([launcher](std::stop_token stop) {
    launcher->serve([](const Json &request) { return Json{{"error", 0}, {"op", request["op"]}}; }, stop);
  });
  PlatformRelay relay(runtime);
  auto endpoint = relay.endpoint();
  auto colon = endpoint.find(':'), space = endpoint.find(' ');
  auto port = std::stoi(endpoint.substr(colon + 1, space - colon - 1));
  auto secret = endpoint.substr(space + 1);
  CHECK(endpoint.starts_with("127.0.0.1:"));
  CHECK(secret.size() == 64);
#ifdef _WIN32
  WSADATA data;
  REQUIRE(WSAStartup(MAKEWORD(2, 2), &data) == 0);
#endif
  auto s = ::socket(AF_INET, SOCK_STREAM, 0);
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_port = htons(std::uint16_t(port));
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  REQUIRE(::connect(s, reinterpret_cast<sockaddr *>(&address), sizeof(address)) == 0);
  auto line = Json{{"secret", secret}, {"op", "entitlement"}}.dump() + "\n";
  REQUIRE(::send(s, line.data(), int(line.size()), 0) == int(line.size()));
  std::string reply;
  char buffer[256];
  for (int n; (n = int(::recv(s, buffer, sizeof(buffer), 0))) > 0;)
    reply.append(buffer, size_t(n));
#ifdef _WIN32
  closesocket(s);
  WSACleanup();
#else
  ::close(s);
#endif
  CHECK(reply == Json{{"error", 0}, {"op", "entitlement"}}.dump() + "\n");
  runtime->close();
  launcher->close();
}
