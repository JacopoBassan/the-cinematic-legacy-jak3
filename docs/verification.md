# Jak 3 verification

The mod targets Windows x64, [Apple Silicon macOS](macos.md) and [experimental Linux x64](linux.md) with official OpenGOAL **v0.3.9**. The launcher protocol was checked with **v2.11.2**. Intel Macs are unsupported. Linux launcher import and gameplay still need testing.

## Source checks

On 2026-11-11, the game-specific suite completed 46 tests on Apple Silicon: 42 passed and four Windows-only cases were skipped. Twelve portable filesystem fixtures also passed on this Mac.

On Windows, all 46 tests and 13 installer filesystem checks passed on 2026-11-17.

Coverage includes wrong-game inputs, corrupt packages, source inventories, staged writes, rollback, interrupted transactions, hardlinks and lock ownership. The Windows checks also cover junctions, paths and child-process cleanup. These source checks are separate from player testing.

[`builder/source-files.json`](../builder/source-files.json) separates the complete `sourceFiles` developer ZIP from the smaller `runtimeFiles` installer source set. Package checks verify each manifest against its own file set. They also check pinned dependencies, runtime versions, Windows adapter provenance and Mac/Linux executable modes. Game models and textures are excluded from distributable source and installer archives.

The Windows installer archive and its source and runtime inventories were checked. The pinned Windows Python runtime also passed a SciPy sparse-factorization smoke test. Player checks cover representative scenes and poses, not a full campaign.

## Linux package and installer checks

Original input checks passed for the supported EU and USA discs with the pinned Linux x64 tools in Ubuntu 24.04 under Rosetta. The required native input fingerprints are recorded in [the Linux profile](../builder/platforms/linux-x64.json). These checks did not run cinematic conversion or gameplay.

The Linux workflow uses Ubuntu 24.04 x64. It builds the final TAR.GZ and checks archive hashes, executable modes, source files, the bundled Python runtime and native dependency operations.

Launcher builds prefer the source shipped with the pinned OpenGOAL tools. The launcher merges source files into the active game folder, where older files can remain. The builder checks the selected source, the player’s disc and generated game files.

A clean Jak 3 EU extraction with OpenGOAL v0.3.9 completed in Ubuntu 24.04 under Rosetta. The patched builder produced and verified all 16 payload files. The FR3 check accepted a fixture changed only in an unused environment-map ID and rejected a change to a checked byte. Launcher GUI and SteamOS gameplay still need testing.

The `destrack.fr3` fingerprint uses fixed values for the two eye IDs that OpenGOAL emits for Kleiver’s driving model without an eye controller. The mod assigns those eyes from the cinematic model. The fingerprint still checks the texture IDs, model geometry and eye IDs for other actors. The full USA conversion passed on Ubuntu 24.04 under Rosetta with a fixture that reproduced the reported fingerprint.

Asset-free installer fixtures cover first install, updates, input and save preservation, wrong-game and running-game refusal, cancellation, rollback, child-process cleanup and retry. They use synthetic files and do not drive the real launcher or convert real game assets. They also check transaction marker ownership and inherited runtime settings. The workflow stores a `verification-linux-x64` receipt.

Release assembly passed with both builder and standard archive names. Checks covered one download per platform, metadata, source files and release checksums. Missing Linux input and a changed Linux runtime pin were rejected before output was written. These archive fixtures use synthetic runtime files.

## Model checks

Version 0.3.7 uses Dark Jak’s dedicated cutscene mesh with its original silver hair. Light Jak uses the cutscene wings and the existing cinematic body. Apple Silicon checks with the EU disc covered all five armor stages in both forms, the transformation into and out of each form, Dark Jak’s claw attack and jump, and Light Jak’s wing deployment, flight, landing and shield.

The builder covers sixteen replacement placements across twelve model names. It retains the original convoy Torn. Checks cover geometry, morphs, textures, Dark shader changes and scoped archive changes. Native text, minimap and speedrun fixes remain part of the original installation.

The 0.3.1 rider corrections were checked with Windows EU v0.3.9: Kleiver uses the native sleeve bend weights on his cinematic arms, and Sig retains the original rider garment beneath cinematic armor. Matched front and side captures and representative rider poses were inspected. A complete EU conversion reproduced the tested Sig garment and accepted Kleiver model data. The Sig garment stage also passed structural checks against the available USA model data; a complete new USA conversion and USA gameplay were not verified. These corrections have not had a new macOS or Linux gameplay session.

Jinx uses cinematic arm and band geometry retargeted to the gameplay rig's limb directions and lengths. A model-specific shoulder correction reduces the excess torso gap while retaining the fuller arms and leaving elbow and hand matrices unchanged. A complete Windows EU conversion, four standing views and four controlled native idle and walking poses were checked on 2026-11-17. The elbow and hand matrices matched the reference exactly at all four sampled phases. The generated model data, joint weights, UVs, textures, eye routing and morph data are unchanged by the runtime correction. Separate EU and USA model rebuilds reproduced their regional outputs; a complete new USA conversion and USA gameplay were not verified. No new macOS or Linux gameplay checks were performed for this change.

Windows EU player checks on 2026-11-16 cover normal, Dark and Light forms, five armor stages, and sampled native walking and jumping in normal and Light forms. Four cinematic pants vertices are fitted to the original front waist surface to preserve sash clearance; cloth geometry, UVs, weights and visibility controls are unchanged. Scoped USA geometry checks produced the same affected vertex data.

The builder checks original tools, disc revisions and consumed assets before conversion. It validates the generated model data and records payload checksums. Earlier conversions covered the supported EU and USA profiles.

## Historical player coverage

These checks were recorded before the 2026-11-11 repository split.

- **Windows:** Earlier Windows EU checks covered startup, running, jumping, melee, gun actions, gaze, facial controls, armor, cloth, and complete Dark and Light transformation/return cycles. Targeted NPC checks included blink interpolation and rendered endpoints.
- **Apple Silicon:** Earlier Apple Silicon checks included native builds from supported local inputs and a complete Kleiver blink cycle.

## Comparisons and image policy

The [gallery](comparisons/jak3.md) contains 25 matched comparisons for Jak 3. Lossless compression checks preserved the decoded RGB pixels and image dimensions. The [comparison manifest](comparisons/manifest.json) records file hashes and capture details.

Before-and-after images, including launcher thumbnails, must use unchanged screenshot pixels. Only cropping, placement and lossless compression are allowed. Do not use image generation, redraw details, retouch colors or upscale captures. Thumbnail source hashes and crop rectangles are recorded in [`mod-source-provenance.json`](../mod-source-provenance.json).

## Limits

- Shadows retain the original gameplay hulls and can differ from cinematic contours.
- NPCs keep gameplay expression and gaze behavior. The mod adds no gaze-tracking AI or cinematic dialogue lip-sync.
- Player checks cover representative scenes and animations, not a full campaign or every NPC.
- Air transport comparisons cover boarding, hover and cabin views. They do not cover comprehensive flight.
