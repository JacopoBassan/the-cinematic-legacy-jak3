# Install Jak 3: The Cinematic Legacy

Use the Jak 3 **Features > Mods > Add from File** button in OpenGOAL Launcher. Select this game's Windows x64 ZIP or Apple Silicon TAR.GZ without unpacking it. Setup builds the models from your own installed files.

Use official OpenGOAL tooling **v0.3.9**. The launcher protocol was checked with **v2.11.2**. Windows 10 or newer on x64 and Apple Silicon macOS are the target platforms. Intel Macs and Linux are unsupported.

| Region | Supported disc |
| --- | --- |
| EU | SCES-52460 |
| USA | SCUS-97330 |

Setup checks the executable revision and consumed file hashes. A matching disc serial alone is insufficient.
The installer includes Python and its dependencies. No terminal, Blender or separate Python installation is needed. Setup calculates required disk space before copying. Allow room for the installed mod and temporary conversion files.

## Saves and updates

Each mod has its own save folder. Vanilla saves stay in place and are not copied automatically. To continue an existing save, close the games and use the launcher's save-folder controls to copy it into the mod's save folder. Keep the original save.

Keep the local archive filename unchanged when updating. Launcher v2.11.2 uses that name to identify local imports. Use TAR.GZ on macOS; the checked launcher version cannot complete local Mac imports from ZIP.

## Troubleshooting

- **Tooling mismatch:** select v0.3.9, rebuild the vanilla game with it, then retry. Installing the tools alone does not update an older extraction.
- **Game running:** close all OpenGOAL games before setup or rebuild.
- **Insufficient space:** free the amount shown by setup, then retry.
- **Failed conversion:** keep the logs in `OpenGOAL-Cinematic-Builds` beside the OpenGOAL installation. Report the error, disc revision and tooling version. Do not upload extracted assets or saves.
- **Interrupted install:** preserve the reported recovery file, stage and backup. Do not remove a lock while its process is running.

See [Mac support](macos.md) and [verification](verification.md) for the checked scope.
