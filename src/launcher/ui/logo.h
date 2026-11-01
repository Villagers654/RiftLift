// The RiftLift logo (assets/riftlift.svg, embedded at build time) rasterized
// on demand for the window icon and the sidebar brand, the white Steam glyph
// (assets/steam.svg) beside the launcher's Steam choices.
#pragma once
#include <SDL3/SDL.h>

namespace riftlift::launcher::ui {
// A `pixels` x `pixels` RGBA surface owned by the caller, or null.
SDL_Surface* logo_surface(int pixels);
// The application icon (ui/app_icon.h): the logo on its bordered tile, for the window icon.
SDL_Surface* icon_surface(int pixels);
SDL_Surface* steam_logo_surface(int pixels);
}  // namespace riftlift::launcher::ui
