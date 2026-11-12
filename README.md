# Jak 3: The Cinematic Legacy

Play Jak 3 with the cinematic character models. Includes Jak, Daxter, transformations, selected NPCs and air transport.

Other games: [Jak and Daxter](https://github.com/JacopoBassan/the-cinematic-legacy-jak1) · [Jak II](https://github.com/JacopoBassan/the-cinematic-legacy-jak2)

[![Original and Cinematic comparison](docs/comparisons/jak3/jak-0.webp)](docs/comparisons/jak3.md)

[All comparisons](docs/comparisons/jak3.md) · [Behind the scenes](https://github.com/JacopoBassan/the-cinematic-legacy-jak1/blob/main/docs/behind-the-scenes.md)

## Why I made this

Jak and Daxter is probably my favourite trilogy of all time, and I spent countless hours playing these games when they first came out on PS2. I was already replaying them through PCSX2 when I found out about OpenGOAL, so you can imagine how happy I was.

Seeing the games in widescreen and at higher resolutions got me wondering what they would look like with the more detailed cutscene models during normal gameplay. I first asked about this on the OpenGOAL Discord [on 2 August 2024](https://discord.com/channels/756287461377703987/949828064026714174/1268810551509909575), and the replies explained why it wouldn’t be a simple swap: different skeletons, missing eye and facial animation, and a model importer that couldn’t bring over the facial blend shapes.

Two years later, I finally decided to have a go at making it work myself, and there turned out to be quite a bit more to it than swapping the models. I had to adapt them to the gameplay skeletons, fit their facial shapes to the games’ existing expression controls and rebuild the model data needed to make those deformations work. Eyes, jaw and brow movement, hands and clothing needed their own attention too.

It took quite a bit of work, but here we are! These are Naughty Dog’s original models from the games, adapted for gameplay, and I hope you enjoy them as much as I do.

I’ve put a lot of care into checking these models, though full playthroughs still need testing. If I’ve missed anything, just let me know and I’ll get it fixed! I might already be working on a few little touch-ups here and there.

[A look behind the scenes](https://github.com/JacopoBassan/the-cinematic-legacy-jak1/blob/main/docs/behind-the-scenes.md)

## Models and forms

✅ Included in the mod.

<table>
<thead><tr><th scope="col">Model / form</th><th scope="col">Jak 1</th><th scope="col">Jak II</th><th scope="col">Jak 3</th></tr></thead>
<tbody>
<tr><th scope="row">Jak</th><td>✅</td><td>✅</td><td>✅</td></tr>
<tr><th scope="row">Daxter</th><td>✅</td><td>✅</td><td>✅</td></tr>
<tr><th scope="row">Dark Jak</th><td>-</td><td>✅</td><td>✅</td></tr>
<tr><th scope="row">Light Jak</th><td>-</td><td>-</td><td>✅</td></tr>
<tr><th scope="row">Samos</th><td>✅</td><td>✅¹</td><td>-</td></tr>
<tr><th scope="row">Keira</th><td>✅</td><td>✅¹</td><td>-</td></tr>
<tr><th scope="row">Sig</th><td>-</td><td>✅</td><td>✅²</td></tr>
<tr><th scope="row">Ashelin</th><td>-</td><td>✅</td><td>✅</td></tr>
<tr><th scope="row">Damas (rider)</th><td>-</td><td>-</td><td>✅</td></tr>
<tr><th scope="row">Kleiver (rider)</th><td>-</td><td>-</td><td>✅</td></tr>
<tr><th scope="row">Jinx</th><td>-</td><td>-</td><td>✅</td></tr>
<tr><th scope="row">Crimson Guard</th><td>-</td><td>-</td><td>✅</td></tr>
<tr><th scope="row">Cyber Errol</th><td>-</td><td>-</td><td>✅</td></tr>
<tr><th scope="row">Air transport</th><td>-</td><td>-</td><td>✅</td></tr>
</tbody>
</table>

¹ Jak II: stadium Samos and Keira. Their other scenes already use cinematic models. ² Jak 3: Sig’s rider and convoy models.

## Install

*Coming to JakMods · WIP*

<table>
<thead><tr><th scope="col">Platform</th><th scope="col">Launcher install</th><th scope="col">Gameplay checks</th></tr></thead>
<tbody>
<tr><th scope="row">Windows x64</th><td>✅ Tested</td><td>✅ Tested</td></tr>
<tr><th scope="row">macOS · Apple Silicon</th><td>✅ Tested</td><td>✅ Tested</td></tr>
<tr><th scope="row">Linux x64</th><td>Experimental</td><td>Not tested</td></tr>
<tr><th scope="row">macOS · Intel</th><td>Not supported</td><td>Not tested</td></tr>
</tbody>
</table>

Gameplay checks cover selected scenes and animations. Linux launcher import, model conversion and gameplay are not yet validated.

Requires official OpenGOAL **v0.3.9** and a supported EU or USA game installation.

1. Install vanilla Jak 3 in [OpenGOAL Launcher](https://opengoal.dev), then close the game.
2. Download the **Windows ZIP**, **Mac TAR.GZ** or experimental **Linux TAR.GZ** from [Releases](https://github.com/JacopoBassan/the-cinematic-legacy-jak3/releases).
3. In the launcher, select the game, open **Features > Mods > Add from File**, and select the archive without unpacking it.
4. Wait for setup, then press **Play**.

The installer builds the replacements from your installed game files. No game models or textures are included. Python is included.

**Saves:** Vanilla OpenGOAL saves are not imported automatically. You can copy your existing progress into the mod’s save folder and keep the original. This has been tested on Windows. [How to copy your saves](https://github.com/JacopoBassan/the-cinematic-legacy-jak1/blob/main/docs/copy-saves.md).

[Compatibility and tested coverage](docs/verification.md) · [Mac support](docs/macos.md) · [Experimental Linux](docs/linux.md) · [Build from source](docs/building.md)

## Development

Based on [OG-Mod-Base](https://github.com/OpenGOAL-Mods/OG-Mod-Base) and [OpenGOAL](https://github.com/open-goal/jak-project). The template release metadata and licences are retained; the game uses the official installed engine and tools. Model conversion is in `builder/`; the mod uses the pinned official runtime.

Run `python tools/verify_mod_repository.py` and `python tools/run_tests.py` for verification. On macOS, use `TMPDIR=/private/tmp` when running the regression suite.

The **Cut Mod Release** action builds the Windows, Apple Silicon and experimental Linux x64 packages. It uses the template metadata format and leaves the release as a draft for review.

Original mod code: [MOD-LICENSE](MOD-LICENSE). Upstream code keeps its own licences.

Developed by [@jacopobassan](https://jacopobassan.me)

<sub>Crafted with help from Codex</sub>
