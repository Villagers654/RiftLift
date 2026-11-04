add_executable(riftlift_tests
  test_main.cpp
  rift_test.cpp
  playtime_test.cpp
  i18n_test.cpp
  library_test.cpp
  platform_test.cpp
  accounts_test.cpp
  digest_test.cpp
  environment_test.cpp
  log_test.cpp
  paths_test.cpp
  broker_test.cpp
  launch_channel_test.cpp
  debug_report_test.cpp
  search_test.cpp
  auth_test.cpp
  builds_test.cpp
  sign_in_link_test.cpp
  menu_button_test.cpp
)
target_link_libraries(riftlift_tests PRIVATE riftlift_launcher_core doctest::doctest)
target_include_directories(riftlift_tests PRIVATE ${PROJECT_SOURCE_DIR})
# The runtime's own logic, written beside it (tests/runtime/*.cpp).
file(GLOB runtime_tests CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/runtime/*.cpp)
if(RIFTLIFT_BUILD_RUNTIME AND runtime_tests)
  target_sources(riftlift_tests PRIVATE ${runtime_tests})
  target_link_libraries(riftlift_tests PRIVATE riftlift_runtime_core)
endif()
# Child processes for the inherited-pipe tests.
add_executable(riftlift_broker_child broker_child.cpp)
target_link_libraries(riftlift_broker_child PRIVATE riftlift_common)
add_dependencies(riftlift_tests riftlift_broker_child)
target_compile_definitions(riftlift_tests PRIVATE RIFTLIFT_BROKER_CHILD="$<TARGET_FILE:riftlift_broker_child>")
add_executable(riftlift_launch_channel_child launch_channel_child.cpp)
target_link_libraries(riftlift_launch_channel_child PRIVATE riftlift_common)
add_dependencies(riftlift_tests riftlift_launch_channel_child)
target_compile_definitions(riftlift_tests PRIVATE RIFTLIFT_LAUNCH_CHANNEL_CHILD="$<TARGET_FILE:riftlift_launch_channel_child>")
add_test(NAME riftlift_tests COMMAND riftlift_tests)
