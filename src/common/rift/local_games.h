#pragma once
// Games RiftLift did not download: an .exe installed elsewhere, and Steam
// games that ship an Oculus PC mode. Both get a game record and launch
// through the runtime like store installs; their files are never deleted.
#include "common/rift/game.h"
namespace riftlift::rift {
// A record for a local game. `root` is the game folder (default: the .exe's
// folder, or the project root of a UE4 bin/win10 layout).
Game local_game(const fs::path &executable, std::string name = {}, fs::path root = {},
                std::string_view arguments = {});
// Steam's text KeyValues (appmanifest_*.acf, libraryfolders.vdf) as JSON objects.
Json parse_text_vdf(std::string_view text);
std::vector<fs::path> steam_library_roots(const fs::path &steam_root);
// Installed Steam games that use the Oculus PC runtime.
std::vector<Game> steam_oculus_games(const fs::path &steam_root);
// Saves a new local or Steam game under a free slug, keeping an existing
// record's own launch settings when it is the same game.
Game add_game(const fs::path &data, Game game);
} // namespace riftlift::rift
