"""English strings: the authoritative reference every key must have an entry in."""

STRINGS = {
    "shell": {
        "steam_status_unknown": "Steam shortcut status unavailable",
        "steam_already_added": "Already added to Steam",
        "game_actions": "Game actions",
        "search": "Search your library",
        "no_matches": "No games match. Try another title.",
        "welcome": "Your library",
        "welcome_hint": "Sign in to see your Meta Rift games.",
        "activity_status": "Library activity status",
    },
    "app": {
        "name": "RiftLift",
    },
    "setup": {
        "banner_text": (
            "RiftLift's compatibility runtime isn't fully set up - some "
            "games may fail to launch."
        ),
        "run_now": "Set up now",
        "heading": "Compatibility runtime",
        "explanation": (
            "Installs or updates Proton, the Meta runtime bridge, and the "
            "OpenXR/OpenVR compatibility layer this build needs. Safe to "
            "re-run any time."
        ),
        "run": "Run setup",
        "running": "Setting up the compatibility runtime",
        "done": "Compatibility runtime is ready",
        "status_heading": "System status",
        "status_ok": "Everything looks ready.",
        "status_needs_setup": (
            "The compatibility runtime isn't set up yet. Run setup below."
        ),
        "status_no_openxr_runtime": (
            "No OpenXR runtime is active, so games won't be able to start in "
            "VR. Install and start Monado or WiVRn (whichever matches your "
            "headset setup), then check again."
        ),
        "checking": "Checking...",
        "recheck": "Check again",
    },
    "nav": {
        "settings": "Settings",
        "account": "Account",
        "accounts": "Accounts ({count})",
        "sign_in": "Sign In",
        "steam_games": "Steam Games",
        "add_game": "Add Game",
    },
    "library": {
        "title": "Library",
        "installed": "Installed",
        "installed_steam": "Installed Steam Games",
        "not_installed": "Not installed",
        "version": "Version {version}",
        "refresh_tooltip": "Refresh installed games and your Meta library",
    },
    "empty": {
        "title": "No Rift games yet",
        "hint": (
            "Add an owned Meta Rift title to download it and make it ready for OpenXR."
        ),
    },
    "game": {
        "launch": "Launch",
        "install": "Install",
        "files": "Files",
        "launch_options": "Launch options",
        "add_to_steam": "Add to Steam",
        "uninstall": "Uninstall",
        "remove_from_riftlift": "Remove from RiftLift",
        "open_rift_store": "Open in Rift Store ↗",
        "open_steam": "Open in Steam ↗",
        "local": "Local game",
        "not_installed": "Not installed",
        "not_played_yet": "Not played yet",
        "played_for": "{duration} played",
        "about": "About",
    },
    "status": {
        "ready": "Ready",
        "view_activity": "View Activity",
        "signed_in": "Signed in to Meta",
        "signed_out": "Signed out of Meta",
        "busy": "Another operation is already running",
        "now_playing": "Playing",
    },
    "task": {
        "checking_system": "Checking your system",
        "adding_from_steam": "Adding {name} from Steam",
        "added_from_steam": "Added {name} from Steam",
        "launching": "Launching {name}",
        "closed": "{name} closed",
        "adding_to_steam": "Adding {name} to Steam",
        "added_to_steam": "Added {name} to Steam",
        "removing": "Removing {name}",
        "removed": "Removed {name}",
        "refreshing_library": "Refreshing library",
        "library_refreshed": "Library refreshed",
        "adding_local_game": "Adding local game",
        "installed_name": "Installed {name}",
    },
    "activity": {
        "title": "RiftLift activity",
        "heading": "Activity",
    },
    "confirm": {
        "uninstall_deletes_files": (
            "Uninstall {name}?\nThis deletes its downloaded files."
        ),
        "uninstall_keeps_files": (
            "Remove {name} from RiftLift?\nIts files are not touched."
        ),
        "change_language": (
            "Switch to {language}?\nRiftLift will refresh to apply the change."
        ),
    },
    "add_game": {
        "title": "Add a Rift game",
        "heading": "Add to your library",
        "install_heading": "Confirm installation",
        "add_local": "Add a local game…",
        "url_section": "Meta Rift store URL",
        "browse_store": "Browse the Rift / PC VR store…",
        "url_placeholder": "https://www.meta.com/experiences/pcvr/…",
        "paste_valid_link": "Paste a valid Meta Rift store link to continue.",
        "checking_link": "Checking Rift store link…",
        "game_not_found": "This Rift store game could not be found.",
        "link_check_failed": "Could not verify this link. Check your connection.",
        "ready_to_install": "Ready to install {name}.",
        "confirm_install": "Install {name}?",
        "add_to_steam": "Add to Steam when finished",
        "starting_install": "Starting install…",
        "pause_download": "Pause download",
        "pausing_download": "Pausing download…",
        "resume_download": "Resume",
        "download_paused": "Download paused. Completed files are kept; resume when ready.",
        "retry_download": "Retry",
        "finishing_install": "Finishing installation…",
        "sign_in_required": "Sign in again from your library, then retry. Completed files are kept.",
        "download_failed": "Download failed. Check your connection and retry. Completed files are kept.",
        "worker_failed": "The download process stopped. Retry to continue from completed files.",
        "steam_sync_failed": "Installed. Steam shortcut update failed; check Steam, then retry Add to Steam.",
        "phase_preparing_segments": "Preparing segments",
        "phase_downloading": "Downloading",
        "phase_assembling_files": "Assembling files",
        "assembling_progress": "{label}: {done:.1f} / {total:.1f} GB",
        "sign_in_to_verify": (
            "This game is not listed in the store. Sign in to Meta to install "
            "delisted games you own."
        ),
        "sign_in_expired": (
            "Your Meta sign-in has expired. Sign in again from your library to "
            "install delisted games you own."
        ),
        "not_launchable": (
            "Downloaded, but RiftLift cannot launch this build (often a 32-bit "
            "build). Its files stay in your games folder."
        ),
        "version_section": "Version",
        "loading_versions": "Loading available versions…",
        "latest_version": "{label} (latest)",
        "all_versions": "All versions ({count})",
        "all_versions_confirm": (
            "Download all {count} versions of {name}? Each version is a separate "
            "full install and can use a lot of disk space."
        ),
        "installing_version": "Version {index}/{total}",
        "versions_failed": "{failed} of {total} versions could not be downloaded:",
        "version_not_launchable": "downloaded, but RiftLift cannot launch it (often a 32-bit build)",
        "version_failed": "download failed",
    },
    "local_game": {
        "title": "Add a local VR game",
        "hint": (
            "Choose an installed Windows VR game. RiftLift leaves its files in place."
        ),
        "executable": "Game executable",
        "name": "Name",
        "name_placeholder": "Filled from the executable",
        "arguments": "Launch arguments (optional)",
        "artwork": "Cover image (optional)",
        "add": "Add",
        "browse": "Browse…",
    },
    "steam_games": {
        "title": "Steam games with Oculus mode",
        "heading": "Add an installed Steam VR game",
        "explanation": (
            "RiftLift scans your installed Steam games for a compatible Oculus "
            "mode. Adding one does not download or duplicate the game."
        ),
        "accessible_name": "Compatible Steam games",
        "scanning": "Scanning installed Steam games…",
        "scan_again": "Scan again",
        "add_to_riftlift": "Add to RiftLift",
        "refresh_in_riftlift": "Refresh in RiftLift",
        "already_in_riftlift": " (already in RiftLift)",
        "none_found": (
            "No compatible games were found. Install a Steam game with an "
            "Oculus mode, then choose Scan again."
        ),
        "found_one": (
            "Found 1 compatible Steam game. "
            "Select one to add it to your RiftLift library."
        ),
        "found_other": (
            "Found {count} compatible Steam games. "
            "Select one to add it to your RiftLift library."
        ),
    },
    "auth": {
        "title": "Meta account",
        "heading": "Sign in to Meta",
        "explanation": (
            "RiftLift opens your default browser with your usual profile "
            "and returns here when Meta finishes. Allow the browser to open RiftLift "
            "when prompted. Your password "
            "and security codes go only to Meta."
        ),
        "open_browser": "Open default browser",
        "sign_out_all": "Sign out of all accounts",
        "sign_out": "Sign out",
        "sign_out_named": "Sign out {name}",
        "unnamed_account": "Meta account {number}",
        "add_account": "Add another account",
        "signed_in_many": "RiftLift is signed in to {count} Meta accounts. Games owned by any of them appear in your library.",
        "signed_in": "RiftLift is signed in to Meta.",
        "opening_browser": "Opening your default browser…",
        "preparing": "Preparing a secure Meta sign-in…",
        "cancel_sign_in": "Cancel sign-in",
        "waiting_for_meta": "Waiting for Meta in {browser}…",
        "browser_open_failed": (
            "Could not open the browser for Meta sign-in. Try again when ready."
        ),
        "finishing": "Finishing sign-in securely…",
        "signed_in_returning": "Signed in. Returning to RiftLift…",
        "try_again": "Try again",
        "signed_out": "Signed out. Open your default browser when ready.",
    },
    "settings": {
        "windows_system_check_explanation": "Checks the Windows VR runtime and writes a diagnostic report to Activity.",
        "windows_debug_logging_tooltip": "Include Windows launcher and VR bridge diagnostics in Activity.",
        "title": "Settings",
        "language": "Language",
        "troubleshooting": "Troubleshooting",
        "debug_logging": "Debug logging",
        "debug_logging_tooltip": (
            "Capture Proton, Wine XR/Steam/Vulkan, DXVK, VKD3D, loader, and "
            "crash diagnostics for future System reports. Storage is limited."
        ),
        "system_check_explanation": (
            "Runs a set of checks on your Proton/OpenXR/SteamVR setup and "
            "uploads a shareable, redacted report - useful when asking for "
            "help troubleshooting a game."
        ),
        "run_system_check": "Generate a diagnostic report",
    },
    "action": {
        "cancel": "Cancel",
        "close": "Close",
        "yes": "Yes",
        "no": "No",
        "ok": "OK",
    },
    "launch_options": {
        "title": "Launch options — {name}",
        "arguments_label": "Additional launch arguments",
        "arguments_placeholder": '--example "value with spaces"',
        "arguments_hint": (
            "Added to the game's default arguments. Quote values containing "
            "spaces. Enter game arguments here, not a shell command or "
            "%command%."
        ),
        "overrides_label": "DLL overrides",
        "overrides_placeholder": "version=n,b;winhttp=n,b",
        "overrides_hint": (
            "Separate rules with semicolons. n = native, b = built-in; "
            "version= disables that DLL. Blank uses inherited settings and "
            "automatic mod-loader detection. These choices apply only to "
            "this game."
        ),
        "environment_label": "Environment variables",
        "environment_placeholder": "PROTON_LOG=1\nPROTON_USE_WINED3D=1",
        "environment_hint": (
            "One NAME=value per line. Values are literal: do not add shell "
            "quotes or export. Saved values override inherited settings for "
            "this game. DLL rules above take precedence over "
            "WINEDLLOVERRIDES entered here."
        ),
        "save_error_title": "Could not save launch options",
        "environment_format_error": (
            "Enter environment variables as NAME=value, one per line"
        ),
    },
}
