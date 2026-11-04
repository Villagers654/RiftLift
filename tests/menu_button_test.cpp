#include "runtime/windows-openvr/MenuButton.h"
#include <doctest/doctest.h>

using namespace riftlift::menu;

TEST_CASE("menu button: choices survive RIFTLIFT_MENU") {
  for (const Choice &c : {Choice{}, Choice{Mode::off}, Choice{Mode::button, B}, Choice{Mode::hold, Y, 0, 1000},
                          Choice{Mode::both, LThumb, RThumb, 500}})
    CHECK(decode(encode(c)) == c);
  CHECK(encode(Choice{Mode::both, LThumb, RThumb, 2000}) == "both:lstick:rstick:2000");
  // Anything else is Automatic.
  for (const char *bad : {"", "sometimes", "button", "button:z", "hold:b", "hold:b:750", "both:a:a:500",
                          "both:a:b", "off:x", "button:b:500"})
    CHECK(decode(bad) == Choice{});
}

TEST_CASE("menu button: each way opens the menu") {
  SUBCASE("automatic keeps the bound menu button") {
    Menu menu;
    CHECK(menu.apply(Enter | A, true, 0) == (Enter | A));
    CHECK(menu.apply(LThumb | RThumb, true, 0) == (LThumb | RThumb));
    CHECK(menu.apply(LThumb | RThumb, true, 5) == (LThumb | RThumb));
  }
  SUBCASE("automatic without a menu button: both stick clicks held 0.5 s") {
    Menu menu;
    CHECK(menu.apply(LThumb | RThumb, false, 1.0) == (LThumb | RThumb));
    CHECK(menu.apply(LThumb | RThumb, false, 1.4) == (LThumb | RThumb));
    CHECK(menu.apply(LThumb | RThumb, false, 1.5) == (LThumb | RThumb | Enter));
    CHECK(menu.apply(LThumb, false, 1.6) == LThumb);
    // Letting go starts the hold over.
    CHECK(menu.apply(LThumb | RThumb, false, 1.7) == (LThumb | RThumb));
  }
  SUBCASE("one button stands in for the menu") {
    Menu menu({Mode::button, B});
    CHECK(menu.apply(B | A, true, 0) == (Enter | A));
    CHECK(menu.apply(Enter, true, 0) == 0);  // the old menu button no longer does
  }
  SUBCASE("a held button still reaches the game") {
    Menu menu({Mode::hold, Y, 0, 1000});
    CHECK(menu.apply(Y, true, 0) == Y);
    CHECK(menu.apply(Y | Enter, true, 0.9) == Y);
    CHECK(menu.apply(Y, true, 1.0) == (Y | Enter));
    CHECK(menu.apply(0, true, 1.1) == 0);
  }
  SUBCASE("two buttons held together") {
    Menu menu({Mode::both, A, X, 500});
    CHECK(menu.apply(A, true, 0) == A);
    CHECK(menu.apply(A, true, 1) == A);
    CHECK(menu.apply(A | X, true, 1.2) == (A | X));
    CHECK(menu.apply(A | X, true, 1.7) == (A | X | Enter));
  }
  SUBCASE("off") {
    Menu menu({Mode::off});
    CHECK(menu.apply(Enter | B, true, 0) == B);
    CHECK(menu.apply(LThumb | RThumb, false, 0) == (LThumb | RThumb));
    CHECK(menu.apply(LThumb | RThumb, false, 9) == (LThumb | RThumb));
  }
}
