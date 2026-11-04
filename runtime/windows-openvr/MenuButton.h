#pragma once
// How a game's menu button is reached (Settings > Runtime > Menu button). The
// launcher passes the choice in RIFTLIFT_MENU; the Touch input code applies it
// to every input state, after the bindings, so it works the same under
// SteamVR and xrizer. Self-contained C++17: the runtime and the launcher's
// tests both build it.
#include <cstdint>
#include <cstdlib>
#include <string>
#include <string_view>

namespace riftlift::menu {
// The Touch buttons, as LibOVR's ovrButton bits.
enum : std::uint32_t { A = 0x1, B = 0x2, RThumb = 0x4, X = 0x100, Y = 0x200, LThumb = 0x400, Enter = 0x100000 };

struct Button {
  std::string_view key, label;
  std::uint32_t bit;
};
inline constexpr Button kButtons[] = {{"x", "X", X},
                                      {"y", "Y", Y},
                                      {"a", "A", A},
                                      {"b", "B", B},
                                      {"lstick", "Left stick click", LThumb},
                                      {"rstick", "Right stick click", RThumb}};
// How long a held button or two buttons wait; two buttons can also open it at once.
inline constexpr int kHoldMs[] = {0, 500, 1000, 2000};

// automatic: the controller's own menu button, else both stick clicks together.
// button: one button opens the menu instead of doing its own thing.
// hold: one button held; a shorter press still reaches the game.
// both: two buttons held together; each still reaches the game.
// off: nothing opens the menu.
enum class Mode { automatic, button, hold, both, off };
inline constexpr std::string_view kModes[] = {"automatic", "button", "hold", "both", "off"};

struct Choice {
  Mode mode = Mode::automatic;
  std::uint32_t first = 0, second = 0;
  int hold_ms = 0;
  bool operator==(const Choice &o) const {
    return mode == o.mode && first == o.first && second == o.second && hold_ms == o.hold_ms;
  }
};

inline std::string_view button_key(std::uint32_t bit) {
  for (const auto &b : kButtons)
    if (b.bit == bit) return b.key;
  return {};
}
inline std::uint32_t button_bit(std::string_view key) {
  for (const auto &b : kButtons)
    if (b.key == key) return b.bit;
  return 0;
}

// "automatic", "off", "button:b", "hold:b:1000", "both:lstick:rstick:500".
inline std::string encode(const Choice &c) {
  std::string out(kModes[int(c.mode)]);
  if (c.mode == Mode::button || c.mode == Mode::hold || c.mode == Mode::both) out += ":" + std::string(button_key(c.first));
  if (c.mode == Mode::both) out += ":" + std::string(button_key(c.second));
  if (c.mode == Mode::hold || c.mode == Mode::both) out += ":" + std::to_string(c.hold_ms);
  return out;
}
// Anything malformed is Automatic.
inline Choice decode(std::string_view text) {
  std::string_view parts[4];
  int n = 0;
  while (n < 4) {
    auto colon = text.find(':');
    parts[n++] = text.substr(0, colon);
    if (colon == std::string_view::npos) break;
    text.remove_prefix(colon + 1);
  }
  Choice c;
  int mode = -1;
  for (int i = 0; i < 5; ++i)
    if (parts[0] == kModes[i]) mode = i;
  auto hold = [](std::string_view ms) {
    for (int allowed : kHoldMs)
      if (ms == std::to_string(allowed)) return allowed;
    return -1;
  };
  switch (Mode(mode)) {
  case Mode::off:
    c.mode = n == 1 ? Mode::off : Mode::automatic;
    break;
  case Mode::button:
    if (n == 2 && (c.first = button_bit(parts[1]))) c.mode = Mode::button;
    break;
  case Mode::hold:
    if (n == 3 && (c.first = button_bit(parts[1])) && (c.hold_ms = hold(parts[2])) > 0) c.mode = Mode::hold;
    break;
  case Mode::both:
    if (n == 4 && (c.first = button_bit(parts[1])) && (c.second = button_bit(parts[2])) && c.first != c.second &&
        (c.hold_ms = hold(parts[3])) >= 0)
      c.mode = Mode::both;
    break;
  default:
    break;
  }
  return c.mode == Mode::automatic ? Choice{} : c;
}

// The choice applied to one input state at a time.
class Menu {
public:
  explicit Menu(Choice choice = {}) : choice_(choice) {}
  // `buttons`: both hands' ovrButton bits, Enter from the bindings. `bound`:
  // whether the bindings give this controller a menu button at all.
  // `now`: seconds, increasing.
  std::uint32_t apply(std::uint32_t buttons, bool bound, double now) {
    switch (choice_.mode) {
    case Mode::off:
      return buttons & ~Enter;
    case Mode::automatic:
      if (bound) {
        since_ = -1;
        return buttons;
      }
      return held(buttons, LThumb | RThumb, 0, now) ? buttons | Enter : buttons;
    case Mode::button: {
      const bool pressed = (buttons & choice_.first) != 0;
      buttons &= ~(choice_.first | Enter);
      return pressed ? buttons | Enter : buttons;
    }
    case Mode::hold:
      buttons &= ~Enter;
      return held(buttons, choice_.first, choice_.hold_ms, now) ? buttons | Enter : buttons;
    case Mode::both:
      buttons &= ~Enter;
      return held(buttons, choice_.first | choice_.second, choice_.hold_ms, now) ? buttons | Enter : buttons;
    }
    return buttons;
  }

private:
  bool held(std::uint32_t buttons, std::uint32_t need, int ms, double now) {
    if ((buttons & need) != need) {
      since_ = -1;
      return false;
    }
    if (since_ < 0) since_ = now;
    return now - since_ >= ms / 1000.0;
  }
  Choice choice_;
  double since_ = -1;
};

inline Choice from_environment() {
  const char *text = std::getenv("RIFTLIFT_MENU");
  return text ? decode(text) : Choice{};
}
} // namespace riftlift::menu
