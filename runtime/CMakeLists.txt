cmake_minimum_required(VERSION 3.21)

project(RiftLiftRuntime LANGUAGES CXX)

find_package(OpenXR 1.0 REQUIRED)

add_executable(riftlift-runtime-host native/host.cpp)
target_compile_features(riftlift-runtime-host PRIVATE cxx_std_20)
target_compile_options(riftlift-runtime-host PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(riftlift-runtime-host PRIVATE OpenXR::openxr_loader ${CMAKE_DL_LIBS})

include(GNUInstallDirs)
install(TARGETS riftlift-runtime-host RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
