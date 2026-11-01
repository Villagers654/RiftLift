add_executable(riftlift_tests
  test_main.cpp
  rift_test.cpp
  playtime_test.cpp
  i18n_test.cpp
  library_test.cpp
  platform_test.cpp
)
target_link_libraries(riftlift_tests PRIVATE riftlift_launcher_core doctest::doctest)
target_include_directories(riftlift_tests PRIVATE ${PROJECT_SOURCE_DIR})
# The runtime's own logic, written beside it (tests/runtime/*.cpp).
file(GLOB runtime_tests CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/runtime/*.cpp)
if(RIFTLIFT_BUILD_RUNTIME AND runtime_tests)
  target_sources(riftlift_tests PRIVATE ${runtime_tests})
  target_link_libraries(riftlift_tests PRIVATE riftlift_runtime_core)
endif()
add_test(NAME riftlift_tests COMMAND riftlift_tests)
