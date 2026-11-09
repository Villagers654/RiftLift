cmake_minimum_required(VERSION 3.28)
# Populate pinned sources directly rather than configuring a sub-build per pin.
if(POLICY CMP0168)
  if(NOT DEFINED CMAKE_POLICY_DEFAULT_CMP0168)
    set(CMAKE_POLICY_DEFAULT_CMP0168 NEW)
  endif()
  cmake_policy(SET CMP0168 "${CMAKE_POLICY_DEFAULT_CMP0168}")
endif()
project(RiftLift VERSION 1.0.1.1 LANGUAGES C CXX)

option(RIFTLIFT_BUILD_LAUNCHER "Build the launcher window (riftlift); riftlift-cli and the runtime always build" ON)
option(RIFTLIFT_BUILD_RUNTIME "Build riftlift-runtime" ON)
option(RIFTLIFT_BUILD_TESTS "Build tests" ON)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_CXX_SCAN_FOR_MODULES OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)

if(WIN32)
  enable_language(RC)
  add_compile_definitions(WIN32_LEAN_AND_MEAN NOMINMAX UNICODE _UNICODE _WIN32_WINNT=0x0A00)
  # Ship self-contained executables: libc++/libunwind linked statically, UCRT from the OS.
  add_link_options(-static)
else()
  # Set before dependency targets so their static archives are PIC as well.
  set(CMAKE_POSITION_INDEPENDENT_CODE ON)
  # CMP0083: executables link as PIE only after this check (tests included).
  include(CheckPIESupported)
  check_pie_supported(LANGUAGES C CXX)
endif()

# Keep compiler-generated file names relocatable, including linked dependencies.
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
  add_compile_options("$<$<COMPILE_LANGUAGE:C,CXX,ASM>:-ffile-prefix-map=${PROJECT_SOURCE_DIR}=.>"
    "$<$<COMPILE_LANGUAGE:C,CXX,ASM>:-ffile-prefix-map=${PROJECT_SOURCE_DIR}/.deps=deps>")
endif()
include(cmake/Dependencies.cmake)
# Our own code only: dependencies are already declared above.
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
  add_compile_options("$<$<COMPILE_LANGUAGE:CXX>:-Wall;-Wextra;-Wno-missing-field-initializers>")
endif()

add_subdirectory(src/common)
if(RIFTLIFT_BUILD_RUNTIME)
  add_subdirectory(src/runtime)
endif()
add_subdirectory(src/launcher)
if(RIFTLIFT_BUILD_TESTS)
  enable_testing()
  add_subdirectory(tests)
endif()
