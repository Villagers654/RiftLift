#include "launcher/exit_report.h"
#include "launcher/app.h"
#include "common/rift/local_games.h"
#include "launcher/i18n.h"
#include "common/file_ops.h"
#include "common/log.h"
#include <chrono>
#include <csignal>
#include "launcher/ui/logo.h"
#include "launcher/ui/style.h"
#include "launcher/window_style.h"
#include <imgui_impl_sdl3.h>
#include <imgui_impl_sdlrenderer3.h>
#define STB_IMAGE_WRITE_IMPLEMENTATION
#define STBIW_WINDOWS_UTF8
#include <stb_image_write.h>

namespace riftlift::launcher {
namespace {
volatile std::sig_atomic_t exit_signal = 0;
void request_signal_exit(int signal) { exit_signal = signal; }
// Developer aid for reviewing the UI without a visible desktop.
void save_screenshot(SDL_Renderer *renderer, const fs::path &file) {
  SDL_Surface *raw = SDL_RenderReadPixels(renderer, nullptr);
  SDL_Surface *rgba =
      raw ? SDL_ConvertSurface(raw, SDL_PIXELFORMAT_RGBA32) : nullptr;
  SDL_DestroySurface(raw);
  if (!rgba)
    throw std::runtime_error("Could not read back the launcher frame.");
  bool ok = stbi_write_png(paths::utf8(file).c_str(), rgba->w, rgba->h, 4,
                           rgba->pixels, rgba->pitch) != 0;
  SDL_DestroySurface(rgba);
  if (!ok)
    throw std::runtime_error("Could not write the screenshot.");
}
} // namespace
// The UI's scale in window coordinates. Those are physical pixels on Windows but logical units
// on Wayland and macOS, where the renderer already applies the pixel density.
static float ui_scale(SDL_Window *window) {
  const auto density = SDL_GetWindowPixelDensity(window);
  return std::max(1.f, SDL_GetWindowDisplayScale(window) / (density > 0 ? density : 1.f));
}
void App::fit_window() {
  scale_ = ui_scale(window_) * float(settings_.interface_size) / 100;
  int width = int(1380 * scale_), height = int(880 * scale_);
  int min_width = int(900 * scale_), min_height = int(620 * scale_);
  // Never larger than the screen: a compositor squeezes an oversized window, which puts the UI
  // and the mouse out of step.
  SDL_Rect usable{};
  if (SDL_GetDisplayUsableBounds(SDL_GetDisplayForWindow(window_), &usable) && usable.w > 0 && usable.h > 0) {
    width = std::min(width, usable.w * 9 / 10);
    height = std::min(height, usable.h * 9 / 10);
    min_width = std::min(min_width, width);
    min_height = std::min(min_height, height);
  }
  SDL_SetWindowMinimumSize(window_, min_width, min_height);
  SDL_SetWindowSize(window_, width, height);
}
App::App(Options options) : Core(std::move(options)) {
  i18n::set_language(settings_.language);
  settings_games_ = paths::utf8(settings_.games);
  settings_runtime_ =
      settings_.runtime_override() ? paths::utf8(settings_.runtime) : "";
  prefer_native_video_driver();
  if (!SDL_Init(SDL_INIT_VIDEO))
    throw std::runtime_error(std::string(video_unavailable_message()) + " (" + SDL_GetError() + ")");
  window_ = SDL_CreateWindow(
      "RiftLift", 1380, 880,
      SDL_WINDOW_RESIZABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY | SDL_WINDOW_HIDDEN |
          (client_side_decorations() ? SDL_WINDOW_BORDERLESS : 0));
  if (!window_)
    throw std::runtime_error("Could not create the launcher window.");
  fit_window();
  if (client_side_decorations())
    SDL_SetWindowHitTest(
        window_,
        [](SDL_Window *, const SDL_Point *point, void *app) {
          return chrome::hit_test(static_cast<App *>(app)->chrome_, *point);
        },
        this);
  SDL_SetWindowPosition(window_, SDL_WINDOWPOS_CENTERED,
                        SDL_WINDOWPOS_CENTERED);
  renderer_ = SDL_CreateRenderer(window_, nullptr);
  if (!renderer_)
    throw std::runtime_error("Could not create an SDL renderer.");
  SDL_SetRenderVSync(renderer_, 1);
  style_native_window(window_, 26, 27, 30);
  if (SDL_Surface *icon = ui::logo_surface(256)) {
    SDL_SetWindowIcon(window_, icon);
    SDL_DestroySurface(icon);
  }
  wake_event_ = SDL_RegisterEvents(1);
  IMGUI_CHECKVERSION();
  ImGui::CreateContext();
  auto &io = ImGui::GetIO();
  io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
  io.IniFilename = nullptr;
  ui::load_fonts();
  theme();
  ImGui_ImplSDL3_InitForSDLRenderer(window_, renderer_);
  ImGui_ImplSDLRenderer3_Init(renderer_);
  textures_ =
      std::make_unique<TextureCache>(renderer_, data_, [this] { wake(); });
  start();
  if (options_.smoke_install.empty() && !options_.launch_requested)
    SDL_ShowWindow(window_);
  if (options_.smoke_view == "downloads")
    page_ = 1;
  if (options_.smoke_view == "settings" || options_.smoke_view == "settings-end")
    page_ = 2;
  if (options_.smoke_view == "account")
    page_ = 3;
  // The manual sign-in steps, with an example link (nothing is sent to Meta).
  if (options_.smoke_view == "manual-sign-in") {
    page_ = 3;
    signing_in_ = auth_manual_ = true;
    auth_url_ = "https://auth.meta.com/native_sso/confirm?native_app_id=512466987071624&native_sso_etoken=EXAMPLE";
    auth_status_ = "Meta's sign-in window could not open. Sign in manually below.";
  }
  if (options_.smoke_view == "add")
    page_ = 4;
  // The dialog a picked .exe opens, with example values; nothing is added.
  if (options_.smoke_view == "add-local") {
    page_ = 4;
    local_draft_ = LocalDraft{"/home/you/Games/Example/Example.exe", "Example", {}, settings_.steam_add_new};
  }
  if (options_.smoke_view == "launch-settings")
    game_tab_ = 1;
  // The dialog Report issue / Upload debug report shows; nothing is saved or uploaded.
  if (options_.smoke_view == "report")
    report_ = SharedReport{{}, "https://dpaste.com/EXAMPLE00", {}};
  // The update dialog an installed copy shows (nothing downloads until Update now).
  if (options_.smoke_view == "update") {
    const std::string release = "https://github.com/Villagers654/RiftLift/releases/";
    update_checked_ = true;
    update_ = Update{"0.1.1", release + "tag/v0.1.1"};
    update_->method = update::Method::installer;
    update_->file = update::Asset{"RiftLift-windows64-setup.exe", release + "download/v0.1.1/RiftLift-windows64-setup.exe"};
    update_->sums = update::Asset{"SHA256SUMS", release + "download/v0.1.1/SHA256SUMS"};
  }
}
App::~App() {
  shutdown();
  textures_.reset();
  for (auto &[pixels, texture] : logos_)
    SDL_DestroyTexture(texture);
  for (auto &[pixels, texture] : steam_logos_)
    SDL_DestroyTexture(texture);
  if (ImGui::GetCurrentContext()) {
    ImGui_ImplSDLRenderer3_Shutdown();
    ImGui_ImplSDL3_Shutdown();
    ImGui::DestroyContext();
  }
  if (renderer_)
    SDL_DestroyRenderer(renderer_);
  if (window_)
    SDL_DestroyWindow(window_);
  SDL_Quit();
  log::info("launcher", "Launcher closed.");
  log::set_file({});
}
void App::wake() {
  SDL_Event e{};
  e.type = wake_event_;
  SDL_PushEvent(&e);
}
void App::on_refresh() { textures_->retry(); }
void App::on_install_queued() {
  // A smoke that opens a game page keeps it so the page's progress is visible.
  if (options_.smoke_select.empty())
    page_ = 1;
}
void App::smoke_install() {
  if (options_.smoke_install.empty() || smoke_exit_ || refreshing_ || scanning_)
    return;
  const auto app = options_.smoke_install;
  if (!smoke_install_home_)
    smoke_install_home_ = account_key_;
  auto finish = [&](std::string error) {
    smoke_install_error_ = std::move(error);
    smoke_exit_ = smoke_install_error_.empty() ? 0 : 1;
    if (auto job = download::base_job(downloads_->snapshot(), app))
      smoke_install_state_ = download::label(job->state);
    if (smoke_install_error_.empty())
      log::info("launcher", "Smoke install of {} completed.", app);
    else
      log::warn("launcher", "Smoke install of {} failed: {}", app,
                smoke_install_error_);
    // Finding the owner switches accounts as a user would; a smoke leaves the
    // saved active account as it found it.
    if (!smoke_install_home_->empty() && account_key_ != *smoke_install_home_) {
      try {
        switch_account(*smoke_install_home_);
      } catch (...) {
        log::warn("launcher", "Could not restore the active account: {}",
                  exception_text());
      }
    }
  };
  if (!smoke_install_queued_) {
    if (!session_ || options_.offline)
      return finish("Sign in to Meta before installing.");
    // A failed refresh says nothing about ownership.
    if (!refresh_error_.empty())
      return finish("Could not read the Meta library: " + refresh_error_);
    if (game_owners(app).empty()) {
      // An account's library is read while it is active, as when the user
      // switches accounts to find a game.
      smoke_install_tried_.insert(account_key_);
      std::string next;
      for (const auto &a : accounts_)
        if (a.signed_in && !a.needs_reauth &&
            !smoke_install_tried_.contains(a.key)) {
          next = a.key;
          break;
        }
      if (next.empty())
        return finish("No signed-in account owns this game.");
      try {
        switch_account(next);
      } catch (...) {
        smoke_install_tried_.insert(next);
      }
      return;
    }
    auto g = game(app);
    if (!g)
      return finish("Unknown game.");
    try {
      install_for_owner(*g);
    } catch (...) {
      return finish(exception_text());
    }
    smoke_install_queued_ = true;
    return;
  }
  auto job = download::base_job(downloads_->snapshot(), app);
  if (!job)
    return finish("The install job was removed.");
  if (job->state == download::State::failed ||
      job->state == download::State::paused)
    return finish(job->error.empty() ? "The install did not finish."
                                     : job->error);
  if (job->state != download::State::complete)
    return;
  // Judge the files from a scan that started after the job completed.
  if (smoke_install_scan_ == 0) {
    smoke_install_scan_ = 1;
    rescan();
    return;
  }
  auto install = installed(app, false);
  if (!install || !install->complete)
    return finish("The installed files did not pass the size check.");
  finish({});
}
void App::save_settings(bool announce) {
  auto next = settings_;
  next.games = paths::from_utf8(settings_games_).lexically_normal();
  // An empty runtime field means the runtime beside the launcher.
  next.runtime = settings_runtime_.empty()
                     ? library::Settings::defaults(data_).runtime
                     : paths::from_utf8(settings_runtime_).lexically_normal();
  apply_settings(std::move(next), announce);
}
SDL_Texture *App::logo(int pixels) {
  auto [it, inserted] = logos_.try_emplace(pixels, nullptr);
  if (inserted)
    if (SDL_Surface *surface = ui::logo_surface(pixels)) {
      it->second = SDL_CreateTextureFromSurface(renderer_, surface);
      SDL_DestroySurface(surface);
    }
  return it->second;
}
SDL_Texture *App::steam_logo(int pixels) {
  auto [it, inserted] = steam_logos_.try_emplace(pixels, nullptr);
  if (inserted)
    if (SDL_Surface *surface = ui::steam_logo_surface(pixels)) {
      it->second = SDL_CreateTextureFromSurface(renderer_, surface);
      SDL_DestroySurface(surface);
    }
  return it->second;
}
void App::tick() {
  Core::tick();
  take_exe_picks();
  textures_->drain();
  for (auto &g : visible_games())
    textures_->request(g, !options_.offline);
}
void App::choose_exe() {
  // Portal (or zenity) dialogs answer on another thread; picks queue up for
  // the UI thread.
  auto picked = [](void *self, const char *const *files, int) {
    auto *app = static_cast<App *>(self);
    if (!files) {
      log::warn("launcher", "The file dialog failed: {}", SDL_GetError());
      return;
    }
    {
      std::lock_guard lock(app->pick_mutex_);
      for (auto f = files; *f; ++f) app->exe_picks_.push_back(paths::from_utf8(*f));
    }
    app->wake();
  };
  static const SDL_DialogFileFilter filters[] = {{"Windows games", "exe"}};
  SDL_ShowOpenFileDialog(picked, this, window_, filters, 1, nullptr, false);
}

void App::take_exe_picks() {
  std::vector<fs::path> picks;
  {
    std::lock_guard lock(pick_mutex_);
    picks.swap(exe_picks_);
  }
  if (picks.empty()) return;
  // One game at a time: the last pick opens the naming dialog.
  auto exe = picks.back();
  action([&] {
    auto draft = rift::local_game(exe);  // checks it is an .exe in a folder
    local_draft_ = LocalDraft{exe, draft.name, {}, settings_.steam_add_new};
    page_ = 4;
  });
}

void App::report_smoke() {
  if (options_.smoke_report.empty() || smoke_reported_)
    return;
  smoke_reported_ = true;
  Json report{{"signedIn", session_.has_value()},
              {"accounts", accounts_.size()},
              {"offline", options_.offline},
              {"games", visible_games().size()},
              {"installed", std::ranges::count_if(
                                installs_, [](auto &i) { return i.complete; })},
              {"artworkTextures", textures_->size()},
              {"liveDetails", details_.size()},
              {"authPageLoaded", auth_page_loaded_},
              {"playAttempted", smoke_played_},
              {"playRequestedUnixMs", smoke_play_requested_unix_ms_},
              {"renderer", SDL_GetRendererName(renderer_)},
              {"dpiScale", scale_}};
  Json exits = Json::array();
  for (auto &[id, r] : running_)
    if (r.exit)
      exits.push_back({{"appId", id},
                       {"exitCode", *r.exit},
                       {"logBytes", r.tail.size()},
                       {"error", r.failure}});
  report["processes"] = exits;
  std::vector<std::optional<int>> codes;
  for (auto &[id, r] : running_) codes.push_back(r.exit);
  report["exit"] = exit_report(exit_reason_, smoke_exit_.value_or(0), int(exit_signal), codes);
  if (!options_.smoke_install.empty()) {
    const auto &app = options_.smoke_install;
    Json install{{"appId", app},
                 {"ok", smoke_exit_ == 0},
                 {"error", smoke_install_error_}};
    if (!smoke_install_state_.empty())
      install["state"] = smoke_install_state_;
    else if (auto job = download::base_job(downloads_->snapshot(), app))
      install["state"] = download::label(job->state);
    if (auto i = installed(app, false)) {
      install.update({{"directory", paths::utf8(i->directory)},
                      {"versionCode", i->plan.version_code},
                      {"complete", i->complete},
                      {"bytes", i->bytes},
                      {"executable", i->game.executable}});
    }
    report["install"] = install;
  }
  paths::atomic_write(options_.smoke_report, report.dump(2));
  auto screenshot = options_.smoke_report;
  screenshot.replace_extension("bmp");
  auto surface = SDL_RenderReadPixels(renderer_, nullptr);
  if (surface) {
    SDL_SaveBMP(surface, paths::utf8(screenshot).c_str());
    SDL_DestroySurface(surface);
  }
}
int App::run() {
  exit_signal = 0;
  const auto previous_int = std::signal(SIGINT, request_signal_exit);
  const auto previous_term = std::signal(SIGTERM, request_signal_exit);
  auto started = SDL_GetTicks();
  last_input_ = started;
  while (!quit_) {
    if (exit_signal) {
      exit_reason_ = "signal";
      smoke_exit_ = 128 + int(exit_signal);
      break;
    }
    auto frame = SDL_GetTicks();
    SDL_Event event{};
    auto process = [&](const SDL_Event &e) {
      ImGui_ImplSDL3_ProcessEvent(&e);
      if (e.type == SDL_EVENT_QUIT ||
          (e.type == SDL_EVENT_WINDOW_CLOSE_REQUESTED &&
           e.window.windowID == SDL_GetWindowID(window_))) {
        if (exit_reason_.empty())
          exit_reason_ = e.type == SDL_EVENT_QUIT ? "quit_event" : "window_close";
        quit_ = true;
      }
      if (e.type >= SDL_EVENT_KEY_DOWN && e.type <= SDL_EVENT_MOUSE_WHEEL)
        last_input_ = SDL_GetTicks();
      // A dropped .exe opens Add Game with it.
      if (e.type == SDL_EVENT_DROP_FILE && e.drop.data) {
        std::lock_guard lock(pick_mutex_);
        exe_picks_.push_back(paths::from_utf8(e.drop.data));
      }
      if (e.type == SDL_EVENT_WINDOW_DISPLAY_SCALE_CHANGED) {
        scale_ = ui_scale(window_) * float(settings_.interface_size) / 100;
        theme();
      }
    };
    int wait =
        (frame - last_input_ > 1200 && !ImGui::IsAnyItemActive()) ? 100 : 0;
    if (SDL_GetWindowFlags(window_) & SDL_WINDOW_MINIMIZED)
      wait = 250;
    if (wait && SDL_WaitEventTimeout(&event, wait))
      process(event);
    while (SDL_PollEvent(&event))
      process(event);
    tick();
    if (options_.launch_requested) {
      if (!direct_started_ && !scanning_) {
        direct_started_ = true;
        auto found = std::ranges::find_if(installs_, [&](const auto& i) {
          return i.complete && i.plan.package == options_.launch_package;
        });
        if (found == installs_.end()) {
          error("The requested package is not installed.");
          smoke_exit_ = 1;
        } else {
          direct_app_ = found->plan.app_id;
          try { launch_game(direct_app_, play_key(direct_app_)); }
          catch (...) { error(exception_text()); smoke_exit_ = 1; }
        }
      }
      if (direct_started_ && !scanning_ && !launch_pending_.contains(direct_app_)) {
        auto r = running_.find(direct_app_);
        if (r == running_.end()) smoke_exit_ = 1;
        else if (r->second.exit) smoke_exit_ = *r->second.exit;
      }
      if (smoke_exit_) { exit_reason_ = "direct_launch_exit"; break; }
      SDL_Delay(20);
      continue;
    }
    if (options_.smoke_details && !smoke_detail_started_ && !refreshing_ &&
        !scanning_) {
      auto visible = visible_games();
      if (!visible.empty()) {
        smoke_detail_started_ = true;
        select(visible.front().id);
      }
    }
    if (!options_.smoke_select.empty() && !smoke_detail_started_ && !scanning_ &&
        std::ranges::any_of(visible_games(), [&](const meta::Game &g) { return g.id == options_.smoke_select; })) {
      smoke_detail_started_ = true;
      select(options_.smoke_select);
      // The install dialog of a game that is not installed (nothing downloads offline).
      if (options_.smoke_view == "install" && game(options_.smoke_select))
        action([&] { request_install(*game(options_.smoke_select)); });
      // The other confirmations and notices, opened for screenshots (nothing is acted on).
      const auto& v = options_.smoke_view;
      const auto visible = visible_games();
      const auto g = std::ranges::find(visible, options_.smoke_select, &meta::Game::id);
      if (v == "uninstall" && installed(g->id, false)) uninstall_target_ = *installed(g->id, false);
      // The version picker with example builds (nothing is fetched or downloaded).
      if (v == "versions" && g != visible.end()) {
        builds_[options_.smoke_select] = {{"9000000000000003", "3.18.0-beta", "3180", {}, {"BETA"}, false},
                                          {"9000000000000002", "3.17.4", "3174", {}, {"LIVE"}, true},
                                          {"9000000000000001", "3.16.0", "3160", {}, {"LIVE"}, true}};
        install_choices_.clear();
        for (auto& location : launcher::install_locations(settings_, launcher::local_volumes()))
          install_choices_.emplace_back(location, launcher::free_space(location.folder));
        install_choice_ = 0;
        version_choice_ = -1;
        install_versions_ = true;
        install_ask_ = {options_.smoke_select, g->name};
      }
      if (v == "cancel-download")
        cancel_target_ = download::Job{.app_id = g->id, .name = g->name, .done = 1ull << 30, .total = 3ull << 30};
      if (v == "remove-account" && !accounts_.empty()) remove_account_ = accounts_.front().key;
      if (v == "report") report_ = SharedReport{.file = "riftlift-debug-report.txt", .url = "https://dpaste.com/EXAMPLE"};
      if (v == "report-uploading") report_ = SharedReport{.file = "riftlift-debug-report.txt"};
      if (v == "report-failed")
        report_ = SharedReport{.file = "riftlift-debug-report.txt", .error = "The paste service did not answer."};
    }
    // Ownership comes from the library refresh; without a cached library a
    // play before it finishes is refused as not owned.
    if (options_.smoke_play && !smoke_played_ && !scanning_ && !refreshing_) {
      for (auto &g : visible_games())
        if (installed(g.id) &&
            (options_.smoke_select.empty() || options_.smoke_select == g.id)) {
          smoke_played_ = true;
          smoke_play_requested_unix_ms_ = uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(
              std::chrono::system_clock::now().time_since_epoch()).count());
          action([&] {
            play(g);
            save_settings();
          });
          break;
        }
    }
    smoke_install();
    if (options_.smoke_auth && !smoke_auth_opened_ && !refreshing_) {
      smoke_auth_opened_ = true;
      sign_in();
    }
    ImGui_ImplSDLRenderer3_NewFrame();
    ImGui_ImplSDL3_NewFrame();
    ImGui::NewFrame();
    draw();
    ImGui::Render();
    SDL_SetRenderDrawColor(renderer_, 26, 27, 30, 255);
    SDL_RenderClear(renderer_);
    // The backend scales clip rects but not vertices: map window coordinates
    // to the HiDPI framebuffer (Wayland, macOS) through the render scale.
    const auto framebuffer_scale = ImGui::GetIO().DisplayFramebufferScale;
    SDL_SetRenderScale(renderer_, framebuffer_scale.x, framebuffer_scale.y);
    ImGui_ImplSDLRenderer3_RenderDrawData(ImGui::GetDrawData(), renderer_);
    const bool timed_out =
        options_.smoke_seconds &&
        SDL_GetTicks() - started >= uint64_t(options_.smoke_seconds) * 1000;
    if (timed_out && !options_.smoke_install.empty() && !smoke_exit_) {
      smoke_install_error_ = "Timed out before the install finished.";
      smoke_exit_ = 1;
    }
    if (timed_out || smoke_exit_) {
      if (exit_reason_.empty())
        exit_reason_ = timed_out ? "timeout"
                       : (*smoke_exit_ == 0 ? "install_complete" : "install_failed");
      if (!options_.screenshot.empty())
        action([&] { save_screenshot(renderer_, options_.screenshot); });
      quit_ = true;
    }
    SDL_RenderPresent(renderer_);
    auto elapsed = SDL_GetTicks() - frame;
    if (elapsed < 17)
      SDL_Delay(Uint32(17 - elapsed));
  }
  if (exit_reason_.empty()) exit_reason_ = "launcher_quit";
  std::vector<std::optional<int>> codes;
  for (auto &[id, r] : running_) {
    r.exit = r.process.poll();
    codes.push_back(r.exit);
  }
  log::info("launcher", "Exit {}", exit_report(exit_reason_, smoke_exit_.value_or(0),
                                              int(exit_signal), codes).dump());
  action([&] { report_smoke(); });
  std::signal(SIGINT, previous_int);
  std::signal(SIGTERM, previous_term);
  return smoke_exit_.value_or(0);
}
int run(Options options) {
  if (options.invalid_launch) {
    log::error("launcher", "Usage: riftlift --launch <installed.package>");
    return 2;
  }
  const auto report_path = options.smoke_report;
  const bool direct = options.launch_requested;
  try {
    App app(std::move(options));
    return app.run();
  } catch (const std::exception &e) {
    const auto status = exit_report("launcher_exception", 1, 0, {});
    log::error("launcher", "Startup failed: {}", e.what());
    log::error("launcher", "Exit {}", status.dump());
    if (!report_path.empty()) {
      try { paths::atomic_write(report_path, Json{{"exit", status}}.dump(2)); }
      catch (...) {} // Preserve the original failure when the report directory is inaccessible.
    }
    log::error("launcher", "Launcher initialization failed. See system "
                           "graphics configuration and folder permissions.");
    if (!direct) SDL_ShowSimpleMessageBox(
        SDL_MESSAGEBOX_ERROR, "RiftLift",
        "RiftLift could not start. Check graphics drivers and access to the "
        "application data folder.",
        nullptr);
    return 1;
  }
}
} // namespace riftlift::launcher
