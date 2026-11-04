#include "launcher/sign_in_link.h"
#include "common/environment.h"
#include "tests/temp_dir.h"
#include <doctest/doctest.h>
#include <cstdlib>
#include <fstream>
#include <map>
#include <optional>
#include <sstream>

using namespace riftlift;
using namespace riftlift::launcher;

TEST_CASE("sign-in link: an address handed over is received once") {
  test::TempDir data("sign-in-link");
  CHECK(sign_in_link::receive(data.path).empty());
  CHECK(sign_in_link::deliver(data.path, "OCULUS://auth?code=1") == 0);
  CHECK(sign_in_link::receive(data.path) == "OCULUS://auth?code=1");
  CHECK(sign_in_link::receive(data.path).empty());
  // Nothing but an oculus:// address, and only into an absolute folder.
  CHECK(sign_in_link::deliver(data.path, "https://example.com") == 2);
  CHECK(sign_in_link::deliver("relative", "oculus://auth") == 2);
  CHECK(sign_in_link::deliver(data.path, "oculus://" + std::string(20000, 'a')) == 2);
  CHECK(sign_in_link::receive(data.path).empty());
}

#ifndef _WIN32
namespace {
std::string read(const fs::path &file) {
  std::ifstream in(file);
  std::ostringstream out;
  out << in.rdbuf();
  return out.str();
}
} // namespace

TEST_CASE("sign-in link: the oculus:// handler before it comes back") {
  // A profile of its own, read the way a desktop without its own tools does.
  test::TempDir home("sign-in-home");
  std::map<std::string, std::optional<std::string>> saved;
  for (const char *name : {"XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CURRENT_DESKTOP", "DESKTOP_SESSION",
                           "XDG_SESSION_DESKTOP", "KDE_FULL_SESSION", "GNOME_DESKTOP_SESSION_ID", "FLATPAK_ID"}) {
    const char *value = std::getenv(name);
    saved[name] = value ? std::optional<std::string>(value) : std::nullopt;
    unsetenv(name);
  }
  setenv("XDG_DATA_HOME", (home.path / "share").c_str(), 1);
  setenv("XDG_CONFIG_HOME", (home.path / "config").c_str(), 1);
  environment::refresh();
  const auto list = home.path / "config/mimeapps.list";
  const auto entry = home.path / "share/applications/io.github.villagers654.RiftLift.SignIn.desktop";
  test::write(list, "[Default Applications]\nx-scheme-handler/oculus=other.desktop\n");
  test::write(home.path / "share/applications/other.desktop",
              "[Desktop Entry]\nType=Application\nName=Other\nExec=other %u\nMimeType=x-scheme-handler/oculus;\n");

  test::TempDir data("sign-in-data");
  if (sign_in_link::take_over(data.path)) {
    CHECK(read(entry).find("--sign-in-callback \"" + data.path.string() + "\" %u") != std::string::npos);
    CHECK(read(list).find("x-scheme-handler/oculus=io.github.villagers654.RiftLift.SignIn.desktop") != std::string::npos);
    // A second sign-in after one that never ended still knows the one before.
    CHECK(sign_in_link::take_over(data.path));
    sign_in_link::restore(data.path);
    CHECK_FALSE(fs::exists(entry));
    CHECK(read(list).find("x-scheme-handler/oculus=other.desktop") != std::string::npos);

    // With none before, none after.
    test::write(list, "[Default Applications]\n");
    fs::remove(home.path / "share/applications/other.desktop");
    CHECK(sign_in_link::take_over(data.path));
    sign_in_link::restore(data.path);
    CHECK(read(list).find("x-scheme-handler/oculus") == std::string::npos);
  } else {
    MESSAGE("xdg-mime is not installed; only the address hand-over was tested.");
  }

  for (auto &[name, value] : saved)
    if (value)
      setenv(name.c_str(), value->c_str(), 1);
    else
      unsetenv(name.c_str());
  environment::refresh();
}
#endif
