# The launcher without its window (core.h): the GUI and riftlift-cli share it.
add_library(riftlift_launcher_core STATIC core.cpp debug_report.cpp drives.cpp i18n.cpp i18n_fr.cpp updater.cpp ui/rich_text_parse.cpp)
target_link_libraries(riftlift_launcher_core PUBLIC riftlift_common)
if(WIN32)
  target_sources(riftlift_launcher_core PRIVATE platform/win32/auth_browser.cpp)
  target_include_directories(riftlift_launcher_core PRIVATE platform/win32)
  target_include_directories(riftlift_launcher_core SYSTEM PRIVATE ${webview2_SOURCE_DIR}/build/native/include)
  target_compile_options(riftlift_launcher_core PRIVATE -fms-extensions)
  target_link_libraries(riftlift_launcher_core PUBLIC ole32 user32)
else()
  target_sources(riftlift_launcher_core PRIVATE platform/posix/auth_browser.cpp)
endif()

# riftlift-cli: the launcher's commands in a terminal, on the same core.
add_executable(riftlift-cli cli.cpp)
target_compile_definitions(riftlift-cli PRIVATE RIFTLIFT_VERSION="${PROJECT_VERSION}")
target_link_libraries(riftlift-cli PRIVATE riftlift_launcher_core)
if(WIN32)
  add_custom_command(TARGET riftlift-cli POST_BUILD COMMAND ${CMAKE_COMMAND} -E copy_if_different
    ${webview2_SOURCE_DIR}/build/native/x64/WebView2Loader.dll $<TARGET_FILE_DIR:riftlift-cli>/WebView2Loader.dll)
endif()
if(MINGW)
  target_link_options(riftlift-cli PRIVATE -municode)
endif()

if(NOT RIFTLIFT_BUILD_LAUNCHER)
  return()
endif()
add_library(riftlift_imgui STATIC
  ${imgui_SOURCE_DIR}/imgui.cpp
  ${imgui_SOURCE_DIR}/imgui_draw.cpp
  ${imgui_SOURCE_DIR}/imgui_tables.cpp
  ${imgui_SOURCE_DIR}/imgui_widgets.cpp
  ${imgui_SOURCE_DIR}/misc/cpp/imgui_stdlib.cpp
  ${imgui_SOURCE_DIR}/backends/imgui_impl_sdl3.cpp
  ${imgui_SOURCE_DIR}/backends/imgui_impl_sdlrenderer3.cpp
  ${imgui_SOURCE_DIR}/misc/freetype/imgui_freetype.cpp)
target_include_directories(riftlift_imgui SYSTEM PUBLIC ${imgui_SOURCE_DIR} ${imgui_SOURCE_DIR}/backends ${imgui_SOURCE_DIR}/misc/cpp ${imgui_SOURCE_DIR}/misc/freetype)
target_link_libraries(riftlift_imgui PUBLIC SDL3::SDL3-static freetype)
target_compile_definitions(riftlift_imgui PUBLIC IMGUI_ENABLE_FREETYPE IMGUI_USE_WCHAR32)
add_executable(riftlift WIN32 main.cpp app.cpp ui/style.cpp ui/views.cpp ui/chrome.cpp ui/logo.cpp ui/rich_text.cpp assets/texture_cache.cpp)
# The logos ship inside the executable; assets/riftlift.svg and assets/steam.svg
# are their only sources.
file(READ ${CMAKE_CURRENT_SOURCE_DIR}/assets/riftlift.svg RIFTLIFT_LOGO_SVG)
file(READ ${CMAKE_CURRENT_SOURCE_DIR}/assets/steam.svg STEAM_LOGO_SVG)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/assets/riftlift.svg
  ${CMAKE_CURRENT_SOURCE_DIR}/assets/steam.svg)
file(CONFIGURE OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/generated/launcher/logo_svg.h
  CONTENT "#pragma once
inline constexpr char kRiftLiftLogoSvg[] = R\"svg(${RIFTLIFT_LOGO_SVG})svg\";
inline constexpr char kSteamLogoSvg[] = R\"svg(${STEAM_LOGO_SVG})svg\";
" @ONLY)
target_include_directories(riftlift PRIVATE ${CMAKE_CURRENT_BINARY_DIR}/generated)
target_compile_definitions(riftlift PRIVATE RIFTLIFT_VERSION="${PROJECT_VERSION}")
target_link_libraries(riftlift PRIVATE riftlift_launcher_core riftlift_imgui webpdecoder)
target_include_directories(riftlift SYSTEM PRIVATE ${stb_SOURCE_DIR} ${webp_SOURCE_DIR}/src ${nanosvg_SOURCE_DIR}/src)
if(WIN32)
  target_sources(riftlift PRIVATE platform/win32/window_style.cpp)
  target_include_directories(riftlift PRIVATE platform/win32)
  target_compile_options(riftlift PRIVATE -fms-extensions)
  target_link_libraries(riftlift PRIVATE dwmapi)
  # The executable's icon (taskbar, Start menu, shortcuts) is rasterized from assets/riftlift.svg at build time.
  add_executable(riftlift-make-icon platform/win32/make_icon.cpp)
  set_target_properties(riftlift-make-icon PROPERTIES RUNTIME_OUTPUT_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
  target_include_directories(riftlift-make-icon SYSTEM PRIVATE ${stb_SOURCE_DIR} ${nanosvg_SOURCE_DIR}/src)
  set(RIFTLIFT_ICON ${CMAKE_CURRENT_BINARY_DIR}/generated/launcher/riftlift.ico)
  add_custom_command(OUTPUT ${RIFTLIFT_ICON}
    COMMAND riftlift-make-icon ${CMAKE_CURRENT_SOURCE_DIR}/assets/riftlift.svg ${RIFTLIFT_ICON}
    DEPENDS riftlift-make-icon ${CMAKE_CURRENT_SOURCE_DIR}/assets/riftlift.svg ${CMAKE_CURRENT_SOURCE_DIR}/ui/app_icon.h VERBATIM)
  file(CONFIGURE OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/generated/launcher/launcher.rc
    CONTENT "1 ICON \"${RIFTLIFT_ICON}\"\n" @ONLY)
  set_source_files_properties(${CMAKE_CURRENT_BINARY_DIR}/generated/launcher/launcher.rc PROPERTIES OBJECT_DEPENDS ${RIFTLIFT_ICON})
  target_sources(riftlift PRIVATE ${CMAKE_CURRENT_BINARY_DIR}/generated/launcher/launcher.rc ${RIFTLIFT_ICON})
  add_custom_command(TARGET riftlift POST_BUILD COMMAND ${CMAKE_COMMAND} -E copy_if_different
    ${webview2_SOURCE_DIR}/build/native/x64/WebView2Loader.dll $<TARGET_FILE_DIR:riftlift>/WebView2Loader.dll)
else()
  target_sources(riftlift PRIVATE platform/posix/window_style.cpp platform/posix/system_fonts.cpp)
endif()
if(MINGW)
  target_link_options(riftlift PRIVATE -municode)
endif()
