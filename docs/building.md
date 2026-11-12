# Build Jak 3: The Cinematic Legacy

This repository contains only the Jak 3 builder and its required helpers. It uses the OG-Mod-Base release metadata format. The installed official OpenGOAL tools supply the engine.

## Source sets

[`builder/source-files.json`](../builder/source-files.json) lists two file sets:

- `sourceFiles`: the complete developer ZIP, including package tools and guides.
- `runtimeFiles`: the smaller source set embedded in the installer. It excludes package tools, compiler source and developer guides.

Both sets include only this game's code and required resources. [`mod-source-provenance.json`](../mod-source-provenance.json) records their file hashes and source history. Code licences do not cover game assets. Generated models and extracted data must stay local.

## Checks

Run from the repository root in a Python 3.11 environment:

```sh
python -m pip install -r builder/requirements.txt
python tools/verify_mod_repository.py
python tools/run_tests.py
```

The suite runs asset-free checks for this game. Windows also runs 13 native filesystem fixtures. On macOS, `TMPDIR=/private/tmp python tools/run_tests.py` uses a temporary directory with no path alias.

## Local conversion

Use an original supported Jak 3 installation and official tooling v0.3.9. Replace the bracketed paths below. Work and output directories must be new, separate, and outside the game installation.

```sh
python builder/build.py --game-data "<OpenGOAL>/active/jak3/data" --tools "<OpenGOAL>/versions/official/v0.3.9" --work "<new-work-directory>" --output "<new-payload-directory>"
```

The builder selects the native platform. `--platform windows-x64`, `--platform macos-arm64` or `--platform linux-x64` selects it explicitly. Linux conversion is experimental and has not been validated with real game data. Run `python builder/build.py --help` for the supported options. Review [current verification](verification.md) before release.

## Installer packages

Run each command on its matching operating system. The helper downloads and verifies the pinned Python 3.11.18 runtime and installs the pinned dependencies. No game assets are needed for packaging.

```sh
python tools/build_mod_installer.py --platform windows-x64 --output dist/input
python tools/build_mod_installer.py --platform macos-arm64 --output dist/input
python tools/build_mod_installer.py --platform linux-x64 --output dist/input
```

Collect all three resulting archives in `dist/input` on one machine. Then use a new output directory:

```sh
python tools/prepare_mod_release.py --input dist/input --output dist/release
```

Version `0.3.0` produces `windows-v0.3.0.zip`, `macos-arm64-v0.3.0.tar.gz`, `linux-v0.3.0.tar.gz`, `source-v0.3.0.zip`, `metadata.json`, `release-manifest.json` and `SHA256SUMS.txt`. Metadata declares only `jak3`.

The manual **Cut Mod Release** workflow builds all three platforms, verifies the assets, and creates or refreshes a draft. On Ubuntu 24.04 x64, it also runs the [asset-free Linux installer checks](linux.md#developer-checks) against the final archive. Publication requires a separate approval. Original mod code uses [MOD-LICENSE](../MOD-LICENSE). Third-party code and bundled dependencies retain their own licence notices.
