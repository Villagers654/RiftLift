# RiftLift compatibility wiki

This is the evidence-based list of games that have been tested successfully with RiftLift. A game is only listed as **Working** after it rendered real VR frames in the Monado spectator view; interactive titles were also checked beyond a static loading screen where applicable.

Testing used Linux, Monado/OpenXR, and a PSVR2. RiftLift itself is not PSVR2-specific. Results are a compatibility signal, not a guarantee for every Wine, driver, GPU, or game update.

## Tested games

| Game | Build tested | Status | Oculus exclusive? | Notes |
| --- | --- | --- | --- | --- |
| [Aircar](https://store.steampowered.com/app/1073390/Aircar/) | Steam Oculus mode | ✅ Working | No | Also officially supports SteamVR. |
| Echo VR | Community PCVR installation | ✅ Working | Yes, originally | Originally released only in the Oculus ecosystem. The official service and store distribution ended; use the [EchoVR setup guide](https://gist.github.com/Villagers654/d5bf4d11f56fc60d1eab91e7bf3f41c5). |
| [Five Nights at Freddy's: Help Wanted](https://store.steampowered.com/app/732690/FIVE_NIGHTS_AT_FREDDYS_HELP_WANTED/) | Steam Oculus mode | ✅ Working | No | The EULA and tracked-controller input were validated in VR. |
| [Keep Talking and Nobody Explodes](https://store.steampowered.com/app/341800/Keep_Talking_and_Nobody_Explodes/) | Steam Oculus mode | ✅ Working | No | Tested through its Oculus launch path; the normal Steam launch remains unchanged. |
| [Lone Echo](https://www.youtube.com/watch?v=2pmV2mwAV9k) | Meta Rift Store | ✅ Working | Yes | Officially released exclusively for Oculus Rift. |
| Oculus First Contact | Meta Rift Store | ✅ Working | Yes | Oculus' first-party Touch-controller introduction. |
| [StereoPaint](https://store.steampowered.com/app/1920760/StereoPaint/) | Steam build through RiftLift | ✅ Working | No | Real rendering and tracked interaction were validated, not just its loading image. |
| [SUPERHOT VR](https://store.steampowered.com/app/617830/SUPERHOT_VR/) | Steam Oculus mode | ✅ Working | No | Tested through its Oculus launch path; the normal Steam launch remains unchanged. |
| [Vader Immortal: Episode I](https://www.playstation.com/en-us/games/vader-immortal-a-star-wars-vr-series/) | Meta Rift Store | ✅ Working | No | Began as an Oculus title, but the series also received an official PlayStation VR release. Controller tracking was validated in-game. |

## What “Oculus exclusive” means here

**Yes** means the title was officially distributed only for Oculus/Meta hardware or storefronts. **No** means it also has an official release for another PC VR storefront or headset platform. This is separate from whether the particular build tested by RiftLift uses the Oculus SDK.

## Steam Oculus-mode games

For Steam titles, RiftLift adds a separate Oculus-compatible shortcut. It does not replace or alter the game's normal Steam/SteamVR launch behavior.

## Reporting another game

Run:

```bash
riftlift doctor
```

The command includes concise setup details and recent launch evidence, then creates a shareable diagnostic paste. Attach that result when opening a [compatibility report](https://github.com/Villagers654/RiftLift/issues).

Last updated: August 2026.
