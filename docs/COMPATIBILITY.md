# RiftLift compatibility wiki

Compatibility results are listed separately for Windows and Linux. Results can vary with runtime, Wine, driver, GPU, and game updates.

## Tested games

✅ = renders a menu or scene without major rendering errors; ❌ = does not work; ⚠️ = known problems; Untested = not tested. A check does not require a full gameplay pass.

| Game | Build tested | Windows | Linux | Oculus exclusive? | Setup notes |
| --- | --- | --- | --- | --- | --- |
| [Aircar](https://store.steampowered.com/app/1073390/Aircar/) | Steam Oculus mode | Untested | ✅ | No | — |
| Echo VR | Windows: original 34.4.636386.0; Linux: community PCVR installation | ✅ | ✅ | Yes, originally | Windows renders the original client's shutdown notice with hands. Online play requires the community installer and patch; follow the [Echo VR setup guide](https://gist.github.com/Villagers654/d5bf4d11f56fc60d1eab91e7bf3f41c5). Community client untested on Windows. |
| [Five Nights at Freddy's: Help Wanted](https://store.steampowered.com/app/732690/FIVE_NIGHTS_AT_FREDDYS_HELP_WANTED/) | Steam Oculus mode | Untested | ✅ | No | — |
| [Keep Talking and Nobody Explodes](https://store.steampowered.com/app/341800/Keep_Talking_and_Nobody_Explodes/) | Steam Oculus mode | Untested | ✅ | No | — |
| [Lone Echo](https://www.youtube.com/watch?v=2pmV2mwAV9k) | Meta Rift Store; Windows: 3.17.4 | ✅ | ✅ | Yes | Windows: stereo main menu and hands. |
| [Lone Echo 2](https://www.meta.com/experiences/pcvr/lone-echo-ii/1711938725528735/) | Meta Rift Store | Untested | ✅ | Yes | — |
| [Oculus First Contact](https://www.meta.com/experiences/pcvr/oculus-first-contact/1217155751659625/) | Meta Rift Store; Windows: 1.1.9 | ✅ | ✅ | Yes | Windows: stereo room and hands at standing height. |
| [StereoPaint](https://store.steampowered.com/app/1920760/StereoPaint/) | Steam | Untested | ✅ | Yes | — |
| [Stormland](https://www.meta.com/experiences/pcvr/stormland/1360938750683878/) | Meta Rift Store; Windows: Stormland_008 | ✅ | ✅ | Yes | Windows: stereo island scene. |
| [SUPERHOT VR](https://store.steampowered.com/app/617830/SUPERHOT_VR/) | Steam Oculus mode | Untested | ✅ | No | — |
| [Vader Immortal: Episode I](https://www.playstation.com/en-us/games/vader-immortal-a-star-wars-vr-series/) | Meta Rift Store; Windows: 1.1.0 | ✅ | ✅ | Yes | Windows: VR settings menu and hands; simulated button input advances setup. |

Windows results use the local `windows-native` development build with a simulated headset. Images were inspected from game render textures while the desktop was locked; headset presentation and full interactive gameplay remain unverified. These results do not establish compatibility for the pinned release.

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

Last updated: September 2, 2026.
