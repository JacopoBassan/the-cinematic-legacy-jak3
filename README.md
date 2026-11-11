# Jak 3: The Cinematic Legacy

Play Jak 3 with the cinematic character models. Includes Jak, Daxter, transformations, selected NPCs and air transport.

[![Original and Cinematic comparison](docs/comparisons/jak3/jak-0.webp)](docs/comparisons/jak3.md)

[All comparisons](docs/comparisons/jak3.md)

## Install

Windows x64 or Apple Silicon macOS, official OpenGOAL **v0.3.9**, and a supported EU or USA game installation.

1. Install vanilla Jak 3 in [OpenGOAL Launcher](https://opengoal.dev), then close the game.
2. Download the **Windows ZIP** or **Mac TAR.GZ** from [Releases](https://github.com/JacopoBassan/the-cinematic-legacy-jak3/releases).
3. In the launcher, select the game, open **Features > Mods > Add from File**, and select the archive without unpacking it.
4. Wait for setup, then press **Play**.

The installer builds the replacements from your installed game files. No game models or textures are included. Python is included. Vanilla saves stay separate from mod saves.

[Compatibility and tested coverage](docs/verification.md) · [Mac support](docs/macos.md) · [Build from source](docs/building.md)

## The other games

[Jak and Daxter](https://github.com/JacopoBassan/the-cinematic-legacy-jak1) · [Jak II](https://github.com/JacopoBassan/the-cinematic-legacy-jak2)

## Development

Based on [OG-Mod-Base](https://github.com/OpenGOAL-Mods/OG-Mod-Base) and [OpenGOAL](https://github.com/open-goal/jak-project). The template release metadata and licences are retained; the game uses the official installed engine and tools. Model conversion is in `builder/`; the mod uses the pinned official runtime.

Run `python tools/verify_mod_repository.py` and `python tools/run_tests.py` for verification. On macOS, use `TMPDIR=/private/tmp` when running the regression suite.

The **Cut Mod Release** action builds the Windows and Apple Silicon packages. It uses the template metadata format and leaves the release as a draft for review.

Original mod code: [MOD-LICENSE](MOD-LICENSE). Upstream code keeps its own licences.

Developed by [@jacopobassan](https://jacopobassan.me)

<sub>Crafted with help from Codex</sub>
