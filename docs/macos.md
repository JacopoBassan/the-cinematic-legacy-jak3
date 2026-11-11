# Jak 3 on macOS

Use the Apple Silicon TAR.GZ installer with official OpenGOAL tooling **v0.3.9**. The launcher flow was checked with **v2.11.2**. Intel Macs are unsupported.

Install a supported local Jak 3 game first. Then select the TAR.GZ in **Jak 3 > Features > Mods > Add from File**. Leave the archive packed. Setup includes Python and its dependencies.

The checked launcher requires TAR.GZ for local Mac imports. Use the Mac package; Windows executables and generated game code are platform-specific.

## Validation scope

Earlier Apple Silicon checks included native builds from supported local inputs and a complete Kleiver blink cycle. These are historical player checks from before the repository split. Current source and archive checks are listed in [verification](verification.md).

## Developer checks

Use a Python 3.11 environment with the dependencies in `builder/requirements.txt`. Run from the repository root:

```sh
python tools/verify_mod_repository.py
TMPDIR=/private/tmp python tools/run_tests.py
python tools/build_mod_installer.py --platform macos-arm64 --output dist/input
```

The package helper obtains the pinned native runtime. Packaging verifies source hashes, dependency operations, the launcher entry point and executable permissions. Archive validation rejects links, special files and privileged modes.

See [building](building.md) for the Jak 3 conversion command and the separate developer and installer source lists. Conversion work and payload directories must stay outside the installed game.
