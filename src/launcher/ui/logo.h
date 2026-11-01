// The RiftLift icon (assets/io.github.villagers654.RiftLift.svg, embedded at
// build time) rasterized on demand for the window icon and the sidebar brand, the white Steam glyph
// (assets/steam.svg) beside the launcher's Steam choices.
#pragma once
#include <SDL3/SDL.h>

namespace riftlift::launcher::ui {
// A `pixels` x `pixels` RGBA surface owned by the caller, or null.
SDL_Surface* logo_surface(int pixels);
SDL_Surface* steam_logo_surface(int pixels);
}  // namespace riftlift::launcher::ui
