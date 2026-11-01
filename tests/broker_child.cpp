// Child half of broker_test: reads its credential over inherited pipes and
// reports whether the secret also reached its argv or environment.
#include "common/ipc.h"
#include "common/json.h"
#include <cstring>
#include <iostream>
#include <string>
#ifdef _WIN32
#include <windows.h>
#else
extern char **environ;
#endif
int main(int argc, char **argv) {
  try {
    std::uintptr_t read = 0, write = 0;
    for (int i = 1; i < argc; ++i) {
      if (std::string_view(argv[i]) == "--credential-read" && i + 1 < argc)
        read = std::stoull(argv[++i]);
      else if (std::string_view(argv[i]) == "--credential-write" && i + 1 < argc)
        write = std::stoull(argv[++i]);
    }
    auto channel = riftlift::PipeChannel::inherited(read, write);
    auto packet = channel->call(riftlift::Json{{"op", "credentials"}});
    auto token = riftlift::str(packet, "appToken");
    if (token.empty())
      return 2;
    bool argv_secret = false, env_secret = false;
    for (int i = 0; i < argc; ++i)
      argv_secret |= std::string_view(argv[i]).find(token) != std::string_view::npos;
#ifdef _WIN32
    auto *env = GetEnvironmentStringsW();
    std::wstring needle(token.begin(), token.end());
    for (auto *p = env; p && *p; p += wcslen(p) + 1)
      env_secret |= std::wstring_view(p).find(needle) != std::wstring_view::npos;
    if (env)
      FreeEnvironmentStringsW(env);
#else
    for (auto **p = environ; p && *p; ++p)
      env_secret |= std::string_view(*p).find(token) != std::string_view::npos;
#endif
    std::cout << "tokenInArgv=" << argv_secret << " tokenInEnv=" << env_secret
              << " tokenFromPipe=1\n";
    return argv_secret || env_secret ? 1 : 0;
  } catch (...) {
    std::cerr << "Credential pipe test failed\n";
    return 3;
  }
}
