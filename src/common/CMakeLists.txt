add_library(riftlift_common STATIC
  digest.cpp
  environment.cpp
  log.cpp
  paths.cpp
  playtime.cpp
  process.cpp
  steam_library.cpp
  steam_art.cpp
  ipc.cpp
  http/client.cpp
  meta/auth.cpp
  meta/accounts.cpp
  meta/store.cpp
  meta/platform.cpp
  library/library.cpp
  download/manager.cpp
  rift/game.cpp
  rift/detection.cpp
  rift/local_games.cpp
)
target_include_directories(riftlift_common PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(riftlift_common PUBLIC nlohmann_json::nlohmann_json CURL::libcurl PRIVATE miniz)
if(RIFTLIFT_BUILD_LAUNCHER)
  target_compile_definitions(riftlift_common PRIVATE RIFTLIFT_STEAM_ART)
  target_include_directories(riftlift_common SYSTEM PRIVATE ${stb_SOURCE_DIR} ${webp_SOURCE_DIR}/src)
  target_link_libraries(riftlift_common PRIVATE webpdecoder)
endif()
if(WIN32)
  target_sources(riftlift_common PRIVATE platform/win32/digest.cpp platform/win32/paths.cpp platform/win32/secret_store.cpp platform/win32/process.cpp platform/win32/file_ops.cpp platform/win32/pipe.cpp)
  target_link_libraries(riftlift_common PRIVATE crypt32 bcrypt shell32 ole32 uuid advapi32)
else()
  find_package(OpenSSL REQUIRED)
  target_link_libraries(riftlift_common PRIVATE OpenSSL::Crypto)
  target_sources(riftlift_common PRIVATE platform/posix/digest.cpp platform/posix/paths.cpp platform/posix/secret_store.cpp platform/posix/process.cpp platform/posix/file_ops.cpp platform/posix/pipe.cpp platform/posix/dbus.cpp)
  target_link_libraries(riftlift_common PRIVATE ${CMAKE_DL_LIBS})
  find_package(PkgConfig QUIET)
  if(PkgConfig_FOUND)
    pkg_check_modules(LIBSECRET QUIET IMPORTED_TARGET libsecret-1)
    if(TARGET PkgConfig::LIBSECRET)
      # Headers only: secret_store.cpp loads libsecret at run time and falls
      # back to a sealed file, so neither libsecret nor GLib is required.
      target_include_directories(riftlift_common SYSTEM PRIVATE ${LIBSECRET_INCLUDE_DIRS})
      target_compile_definitions(riftlift_common PRIVATE RIFTLIFT_HAVE_LIBSECRET)
    endif()
  endif()
endif()
