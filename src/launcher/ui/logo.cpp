#include "launcher/ui/logo.h"

#include <launcher/logo_svg.h>
#include <algorithm>
#include <string>

#define NANOSVG_IMPLEMENTATION
#define NANOSVG_ALL_COLOR_KEYWORDS
#include <nanosvg.h>
#define NANOSVGRAST_IMPLEMENTATION
#include <nanosvgrast.h>

namespace riftlift::launcher::ui {
namespace {
SDL_Surface* rasterize(std::string source, int pixels) {  // nsvgParse edits its input
  if (pixels <= 0) return nullptr;
  NSVGimage* image = nsvgParse(source.data(), "px", 96);
  if (!image) return nullptr;
  SDL_Surface* surface = nullptr;
  if (image->width > 0 && image->height > 0)
    if (NSVGrasterizer* raster = nsvgCreateRasterizer()) {
      surface = SDL_CreateSurface(pixels, pixels, SDL_PIXELFORMAT_RGBA32);
      if (surface) {
        float scale = float(pixels) / std::max(image->width, image->height);
        nsvgRasterize(raster, image, 0, 0, scale, static_cast<unsigned char*>(surface->pixels), pixels, pixels,
                      surface->pitch);
      }
      nsvgDeleteRasterizer(raster);
    }
  nsvgDelete(image);
  return surface;
}
}  // namespace

SDL_Surface* logo_surface(int pixels) { return rasterize(kRiftLiftLogoSvg, pixels); }
SDL_Surface* steam_logo_surface(int pixels) { return rasterize(kSteamLogoSvg, pixels); }
}  // namespace riftlift::launcher::ui
