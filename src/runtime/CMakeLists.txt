# riftlift-runtime: prepares the compatibility layer for one game and runs it
# (GE-Proton on Linux, RiftLiftLauncher.exe directly on Windows).
#
# The release payloads (riftlift-compat.zip, riftlift-dxvk.tar.gz,
# riftlift-xrizer.tar.gz) are fetched from the GitHub release named after this
# version; installs record it, so a new version refreshes them.
set(RIFTLIFT_PAYLOAD_VERSION "${PROJECT_VERSION}" CACHE STRING
    "RiftLift release whose compatibility payloads the runtime installs")

# Logic shared with the unit tests (tests/runtime/*.cpp link this library).
add_library(riftlift_runtime_core STATIC
  archive.cpp
  child.cpp
  diagnostics.cpp
  files.cpp
  layout.cpp
  meta_runtime.cpp
  platform_relay.cpp
  options.cpp
  rules.cpp
  xr.cpp
)
target_include_directories(riftlift_runtime_core PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_compile_definitions(riftlift_runtime_core PUBLIC
  RIFTLIFT_PAYLOAD_VERSION="${RIFTLIFT_PAYLOAD_VERSION}")
target_link_libraries(riftlift_runtime_core PUBLIC riftlift_common PRIVATE miniz)
if(WIN32)
  target_link_libraries(riftlift_runtime_core PUBLIC ws2_32)
else()
  target_link_libraries(riftlift_runtime_core PUBLIC ${CMAKE_DL_LIBS})
endif()

add_executable(riftlift-runtime main.cpp)
if(WIN32)
  target_sources(riftlift-runtime PRIVATE windows_launch.cpp)
else()
  target_sources(riftlift-runtime PRIVATE linux_launch.cpp proton.cpp)
endif()
target_link_libraries(riftlift-runtime PRIVATE riftlift_runtime_core)
