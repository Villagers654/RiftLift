# Test-only SteamVR driver: two simulated Oculus Touch controllers.
# Build output is a ready-to-register SteamVR driver folder (see README.md).
cmake_minimum_required(VERSION 3.20)
project(RiftLiftTouchSim LANGUAGES CXX)
set(OPENVR_HEADERS "${CMAKE_CURRENT_SOURCE_DIR}/../../Externals/openvr/headers" CACHE PATH "")
set(out "${CMAKE_BINARY_DIR}/riftlift_touchsim")
add_library(driver_riftlift_touchsim SHARED driver_riftlift_touchsim.cpp)
target_include_directories(driver_riftlift_touchsim PRIVATE "${OPENVR_HEADERS}")
target_compile_features(driver_riftlift_touchsim PRIVATE cxx_std_17)
set_target_properties(driver_riftlift_touchsim PROPERTIES PREFIX ""
  LIBRARY_OUTPUT_DIRECTORY "$<1:${out}/bin/win64>" RUNTIME_OUTPUT_DIRECTORY "$<1:${out}/bin/win64>"
  MSVC_RUNTIME_LIBRARY MultiThreaded)
if(MINGW)
  target_link_options(driver_riftlift_touchsim PRIVATE -static)
endif()
configure_file(driver.vrdrivermanifest "${out}/driver.vrdrivermanifest" COPYONLY)
