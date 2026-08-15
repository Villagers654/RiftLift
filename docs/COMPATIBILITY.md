# RiftLift compatibility wiki

These games have been tested successfully with RiftLift. Results can vary with Wine, driver, GPU, and game updates.

## Tested games

| Game | Build tested | Status | Oculus exclusive? | Setup notes |
| --- | --- | --- | --- | --- |
| [Aircar](https://store.steampowered.com/app/1073390/Aircar/) | Steam Oculus mode | ✅ Working | No | — |
| Echo VR | Community PCVR installation | ✅ Working | Yes, originally | Requires the community installer and patch; follow the [Echo VR setup guide](https://gist.github.com/Villagers654/d5bf4d11f56fc60d1eab91e7bf3f41c5). |
| [Five Nights at Freddy's: Help Wanted](https://store.steampowered.com/app/732690/FIVE_NIGHTS_AT_FREDDYS_HELP_WANTED/) | Steam Oculus mode | ✅ Working | No | — |
| [Keep Talking and Nobody Explodes](https://store.steampowered.com/app/341800/Keep_Talking_and_Nobody_Explodes/) | Steam Oculus mode | ✅ Working | No | — |
| [Lone Echo](https://www.youtube.com/watch?v=2pmV2mwAV9k) | Meta Rift Store | ✅ Working | Yes | — |
| [Lone Echo 2](https://www.meta.com/experiences/pcvr/lone-echo-ii/1711938725528735/) | Meta Rift Store | ✅ Working | Yes | — |
| [Oculus First Contact](https://www.meta.com/experiences/pcvr/oculus-first-contact/1217155751659625/) | Meta Rift Store | ✅ Working | Yes | — |
| [StereoPaint](https://store.steampowered.com/app/1920760/StereoPaint/) | Steam | ✅ Working | Yes | — |
| [SUPERHOT VR](https://store.steampowered.com/app/617830/SUPERHOT_VR/) | Steam Oculus mode | ✅ Working | No | — |
| [Vader Immortal: Episode I](https://www.playstation.com/en-us/games/vader-immortal-a-star-wars-vr-series/) | Meta Rift Store | ✅ Working | Yes | — |

## What “Oculus exclusive” means here

**Yes** means the title's PC VR release was available only for Oculus/Meta Rift, though it may also have versions on non-PC consoles. **No** means it also received an official PC VR release for another headset platform. This is separate from whether the particular build tested by RiftLift uses the Oculus SDK.

## Steam Oculus-mode games

For Steam titles, RiftLift adds a separate Oculus-compatible shortcut. It does not replace or alter the game's normal Steam/SteamVR launch behavior.

## Reporting another game

Run:

```bash
riftlift doctor
```

The command includes concise setup details and recent launch evidence, then creates a shareable diagnostic paste. Attach that result when opening a [compatibility report](https://github.com/Villagers654/RiftLift/issues).

Last updated: August 2026.
