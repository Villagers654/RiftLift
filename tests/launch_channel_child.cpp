// Child half of launch_channel_test: fetches a frame from the parent over
// inherited pipes and sends it back unchanged.
#include "common/ipc.h"
#include <string>
int main(int argc, char** argv) {
  try {
    std::uintptr_t r = 0, w = 0;
    for (int i = 1; i + 1 < argc; ++i) {
      const std::string option = argv[i];
      if (option == "--credential-read") r = std::stoull(argv[++i]);
      else if (option == "--credential-write") w = std::stoull(argv[++i]);
    }
    auto pipe = riftlift::PipeChannel::inherited(r, w);
    auto startup = pipe->call({{"op", "details"}});
    auto reply = pipe->call({{"op", "echo"}, {"app", "123"}, {"details", startup.at("details")}});
    return reply.value("success", false) ? 0 : 1;
  } catch (...) { return 2; }
}
