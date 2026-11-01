# Pinned third-party dependencies. Sources are cached under .deps/fetch (see
# CMakePresets.json) so separate build trees share one download.
include(FetchContent)
set(FETCHCONTENT_QUIET ON)

FetchContent_Declare(nlohmann_json
  URL https://github.com/nlohmann/json/archive/55f93686c01528224f448c19128836e7df245f72.tar.gz # v3.12.0
  URL_HASH SHA256=67f4cdd9ca930c9c1e130af4a437c7fc98fab77a2846fc2d2a14b4943831f8ef
  SYSTEM)
set(JSON_BuildTests OFF CACHE INTERNAL "")
set(JSON_Install OFF CACHE INTERNAL "")

FetchContent_Declare(doctest
  URL https://github.com/doctest/doctest/archive/2d0a9359a60c51affe2a9bebb1be1dca47868151.tar.gz # v2.5.3
  URL_HASH SHA256=e64542c4ea68e9f381ccf6eae924cfdd652567c87c142d76fe92644fb4608149
  SYSTEM)
set(DOCTEST_WITH_TESTS OFF CACHE INTERNAL "")
set(DOCTEST_NO_INSTALL ON CACHE INTERNAL "")

FetchContent_Declare(imgui
  URL https://github.com/ocornut/imgui/archive/b48d1afbe8ee8b238e2961dc363a949dd7304e23.tar.gz # v1.92.9b-docking
  URL_HASH SHA256=747c5a465a05c4ef5b4a09e2e0e3d0ce2bc76b31420f0f5af33df470907cf893
  SYSTEM)

FetchContent_MakeAvailable(nlohmann_json doctest)
if(NOT WIN32)
  # As in Venture, tests run without doctest's POSIX signal handlers.
  target_compile_definitions(doctest INTERFACE DOCTEST_CONFIG_NO_POSIX_SIGNALS)
endif()

# Networking is shared by the launcher and the runtime; no UI dependency leaks
# into riftlift_common. Only HTTP(S), static libcurl, native Windows certificate store.
set(BUILD_CURL_EXE OFF CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(BUILD_STATIC_LIBS ON CACHE BOOL "" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(HTTP_ONLY ON CACHE BOOL "" FORCE)
set(CURL_DISABLE_WEBSOCKETS ON CACHE BOOL "" FORCE)
set(BUILD_LIBCURL_DOCS OFF CACHE BOOL "" FORCE)
set(BUILD_MISC_DOCS OFF CACHE BOOL "" FORCE)
set(CURL_DISABLE_INSTALL ON CACHE BOOL "" FORCE)
set(CURL_USE_LIBPSL OFF CACHE BOOL "" FORCE)
set(CURL_USE_LIBSSH2 OFF CACHE BOOL "" FORCE)
set(CURL_USE_LIBSSH OFF CACHE BOOL "" FORCE)
set(CURL_USE_GSSAPI OFF CACHE BOOL "" FORCE)
set(CURL_USE_LIBIDN2 OFF CACHE BOOL "" FORCE)
set(CURL_ZLIB OFF CACHE BOOL "" FORCE)
set(CURL_BROTLI OFF CACHE BOOL "" FORCE)
set(CURL_ZSTD OFF CACHE BOOL "" FORCE)
set(USE_NGHTTP2 OFF CACHE BOOL "" FORCE)
set(CURL_DISABLE_LDAP ON CACHE BOOL "" FORCE)
set(CURL_DISABLE_LDAPS ON CACHE BOOL "" FORCE)
if(WIN32)
  set(CURL_USE_SCHANNEL ON CACHE BOOL "" FORCE)
  set(CURL_USE_OPENSSL OFF CACHE BOOL "" FORCE)
else()
  set(CURL_USE_OPENSSL ON CACHE BOOL "" FORCE)
endif()
FetchContent_Declare(curl
  URL https://github.com/curl/curl/archive/fdb8a789d2b446b77bd7cdd2eff95f6cbc814cf4.tar.gz # curl-8_14_1
  URL_HASH SHA256=64205466b2b9c1fd6fc44a8b765842edd67ed1a3a7df554d68efcf117eca8925
  SYSTEM)
FetchContent_MakeAvailable(curl)

# SDL3: the launcher's window, input and renderer.
if(RIFTLIFT_BUILD_LAUNCHER)
  set(SDL_SHARED OFF CACHE BOOL "" FORCE)
  set(SDL_STATIC ON CACHE BOOL "" FORCE)
  set(SDL_TEST_LIBRARY OFF CACHE BOOL "" FORCE)
  set(SDL_TESTS OFF CACHE BOOL "" FORCE)
  set(SDL_EXAMPLES OFF CACHE BOOL "" FORCE)
  set(SDL_INSTALL OFF CACHE BOOL "" FORCE)
  # Wayland only on Linux: no X11 video backend (Xlib, xcb, XCursor, XInput,
  # XRandR...) is compiled in or loaded at run time. No libdecor either: its
  # cairo/GTK plugins load libX11 and libxcb; the launcher draws its own
  # window chrome instead (launcher/ui/chrome.h).
  set(SDL_X11 OFF CACHE BOOL "" FORCE)
  set(SDL_WAYLAND_LIBDECOR OFF CACHE BOOL "" FORCE)
  FetchContent_Declare(SDL3
    URL https://github.com/libsdl-org/SDL/archive/7f3ae3d57459e59943a4ecfefc8f6277ec6bf540.tar.gz # release-3.2.28
    URL_HASH SHA256=7a8347c770b90b33daac2352858ca03f9c9a2ccc8ce711054870361d1a6b32e5
    SYSTEM)
  FetchContent_MakeAvailable(SDL3)
endif()

if(RIFTLIFT_BUILD_LAUNCHER)
  FetchContent_Declare(stb
    URL https://github.com/nothings/stb/archive/2c980bb59875b0d32144a71867fbdebb2f77cd20.tar.gz
    URL_HASH SHA256=9a955b1b49a4410088a2e0ee2a9c057c3c907d0c1d75454144cb980aca0ba515
    SYSTEM)
  # FreeType rasterizes the launcher's text (hinting, color emoji) instead of stb_truetype.
  foreach(feature ZLIB BZIP2 PNG HARFBUZZ BROTLI)
    set(FT_DISABLE_${feature} ON CACHE BOOL "" FORCE)
  endforeach()
  FetchContent_Declare(freetype
    URL https://github.com/freetype/freetype/archive/0a0221a1347e2f1e07c395263540026e9a0aa7c7.tar.gz # VER-2-14-3
    URL_HASH SHA256=11cd478953fc1d382f20a233b8e2aed6c31b47cbf0568c4bdb3334bcc1550698
    SYSTEM EXCLUDE_FROM_ALL)
  # nanosvg rasterizes the launcher's SVG logo; headers only (src/ has no CMake project).
  FetchContent_Declare(nanosvg
    URL https://github.com/memononen/nanosvg/archive/239e102ec2c691f2902e20ace2ed36ee4a35cfe6.tar.gz
    URL_HASH SHA256=2bc68bdb518d7800252042e5cad50a0ab321596f0cbf49ef2a752926329063d2
    SOURCE_SUBDIR src SYSTEM)
  FetchContent_MakeAvailable(imgui stb freetype nanosvg)
  # Meta's public CDN now serves WebP even when Accept requests JPEG. stb still
  # handles JPEG/PNG/GIF; use the small portable decoder for WebP covers.
  foreach(feature ANIM_UTILS CWEBP DWEBP GIF2WEBP IMG2WEBP VWEBP WEBPINFO WEBPMUX EXTRAS LIBWEBPMUX)
    set(WEBP_BUILD_${feature} OFF CACHE BOOL "" FORCE)
  endforeach()
  set(WEBP_BUILD_LIBWEBPDECODER ON CACHE BOOL "" FORCE)
  set(WEBP_BUILD_FUZZTEST OFF CACHE BOOL "" FORCE)
  FetchContent_Declare(webp
    URL https://github.com/webmproject/libwebp/archive/a4d7a715337ded4451fec90ff8ce79728e04126c.tar.gz # v1.5.0
    URL_HASH SHA256=e3a6189054f81e06a5fbadb0d5c5a4678018c6df6d37e17bd99a86f66c650a97
    SYSTEM EXCLUDE_FROM_ALL)
  FetchContent_MakeAvailable(webp)
  if(WIN32)
    FetchContent_Declare(webview2
      URL https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.3537.50/microsoft.web.webview2.1.0.3537.50.nupkg
      URL_HASH SHA256=5ea526bbd728adda0da4d31219267e96460494a427e4894c4e09d9f320f4b9aa
      DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
    FetchContent_MakeAvailable(webview2)
  endif()
endif()

# Static, pinned upstream release.
if(NOT TARGET zlibstatic)
  set(ZLIB_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
  FetchContent_Declare(zlib
    URL https://zlib.net/fossils/zlib-1.3.1.tar.gz
    URL_HASH SHA256=9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23
    SYSTEM)
  FetchContent_MakeAvailable(zlib)
  set_target_properties(zlib PROPERTIES EXCLUDE_FROM_ALL TRUE)
endif()

# miniz reads Meta's manifest archives and inflates download segments.
FetchContent_Declare(miniz
  BINARY_DIR "${CMAKE_BINARY_DIR}/_deps/miniz-build"
  URL https://github.com/richgel999/miniz/archive/293d4db1b7d0ffee9756d035b9ac6f7431ef8492.tar.gz # 3.0.2
  URL_HASH SHA256=27d99216e9127d1678085639f3059f6907a02c044e8a38392a39f96163ddbbf0
  SYSTEM)
set(BUILD_SHARED_LIBS OFF)
set(BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(CMAKE_POLICY_VERSION_MINIMUM 3.5)
FetchContent_MakeAvailable(miniz)

# DejaVu Sans: the launcher's fallback font, so text renders without
# fontconfig or installed fonts. Files only, no build.
if(NOT WIN32)
  FetchContent_Declare(dejavu_fonts
    URL https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.tar.bz2
    URL_HASH SHA256=fa9ca4d13871dd122f61258a80d01751d603b4d3ee14095d65453b4e846e17d7
    SOURCE_SUBDIR riftlift-no-upstream-targets
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
  FetchContent_MakeAvailable(dejavu_fonts)
endif()
