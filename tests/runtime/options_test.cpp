#include "runtime/archive.h"
#include "runtime/diagnostics.h"
#include "runtime/files.h"
#include "runtime/options.h"
#include <doctest/doctest.h>
#include <format>
#include <fstream>

using namespace riftlift;
using namespace riftlift::runtime;

TEST_CASE("runtime: command line") {
  auto o = parse_options({"--game", "lone-echo", "--log", "/l/runtime.log", "--account", "123", "--parent-pid", "42",
                          "--console", "--run-seconds", "30", "--package", "lone-echo", "--future"});
  CHECK(o.game == "lone-echo");
  CHECK(o.log == "/l/runtime.log");
  CHECK(o.account == "123");
  CHECK(o.parent_pid == 42);
  CHECK(o.console);
  CHECK(o.run_seconds == 30);
  CHECK(o.ignored == std::vector<std::string>{"--package lone-echo", "--future"});
  CHECK(parse_options({"--game", "x"}).account == "local");
  CHECK_THROWS(parse_options({}));
  CHECK_THROWS(parse_options({"--game"}));
  CHECK_THROWS(parse_options({"--game", "x", "--run-seconds", "soon"}));
  CHECK(parse_options({"--probe-openvr", "/x.so"}).probe_openvr == "/x.so");
}

TEST_CASE("runtime: archive members stay inside their folder") {
  CHECK(archive_member_safe("dxvk/x64/d3d11.dll"));
  CHECK(archive_member_safe("Input\\action_manifest.json"));
  CHECK(archive_member_safe("a/../b"));
  CHECK(archive_member_safe("./"));
  CHECK_FALSE(archive_member_safe("../evil"));
  CHECK_FALSE(archive_member_safe("a/../../evil"));
  CHECK_FALSE(archive_member_safe("/etc/passwd"));
  CHECK_FALSE(archive_member_safe("..\\evil"));
  CHECK_FALSE(archive_member_safe("C:\\Windows\\x"));
  CHECK_FALSE(archive_member_safe(""));
#ifndef _WIN32
  CHECK(tar_member_types("drwxr-xr-x u/g 0 2024 dxvk/\n-rw-r--r-- u/g 1 2024 a\nlrwx u/g 0 x -> y\n") == "d-l");
#endif
}

TEST_CASE("runtime: atomic writes, staging and directory replacement") {
  auto root = fs::temp_directory_path() / "riftlift-runtime-test-files";
  fs::remove_all(root);
  write_file(root / "a/b.txt", "hello");
  CHECK(read_file(root / "a/b.txt") == "hello");
  CHECK(sha256_file(root / "a/b.txt") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824");
  CHECK(try_sha256_file(root / "missing").empty());
  fs::path kept;
  {
    Staging staging(root, ".unpack-");
    kept = staging.path();
    write_file(kept / "new.txt", "new");
    write_file(root / "target/old.txt", "old");
    replace_directory(kept, root / "target");
  }
  CHECK_FALSE(fs::exists(kept));
  CHECK(read_file(root / "target/new.txt") == "new");
  CHECK_FALSE(fs::exists(root / "target/old.txt"));
  CHECK_FALSE(fs::exists(root / ".target.riftlift-previous"));
  fs::remove_all(root);
}

TEST_CASE("runtime: diagnostic logs keep their head and tail") {
  auto file = fs::temp_directory_path() / "riftlift-runtime-test-trim.log";
  std::string text;
  for (int i = 0; i < 100; ++i)
    text += std::format("line {:03}\n", i);
  {
    std::ofstream(file, std::ios::binary) << text;
  }
  trim_diagnostic_log(file, 400);
  auto result = read_file(file);
  CHECK(result.size() <= 400);
  CHECK(result.starts_with("line 000\n"));
  CHECK(result.find("[middle diagnostic output truncated]") != std::string::npos);
  CHECK(result.ends_with("line 099\n"));
  trim_diagnostic_log(file, 100000);
  CHECK(read_file(file) == result);
  fs::remove(file);
}
