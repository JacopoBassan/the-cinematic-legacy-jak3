# Jak 3 on Linux

Linux x64 support is experimental. Complete Jak 3 EU and USA conversions passed on Ubuntu with the official Linux x64 tools. SteamOS launcher import and gameplay still need testing.

Use `linux-v0.3.7.tar.gz` from [Releases](https://github.com/JacopoBassan/the-cinematic-legacy-jak3/releases) with official OpenGOAL **v0.3.9**. Python and its dependencies are included.

Supported EU and USA disc revisions are listed in [installation](installation.md). The installer uses the verified source files shipped with OpenGOAL and checks the disc and graphics files from your active game install.

To try the package, select the TAR.GZ in **Jak 3 > Features > Mods > Add from File**. Leave the archive packed.

## Check scope

The Ubuntu 24.04 x64 workflow builds the package and checks its bundled runtime, native dependencies, source files, archive hashes and executable permissions. Synthetic installer fixtures check install, update, rejection and recovery paths without game assets. These checks do not validate real launcher import, model conversion or gameplay. See [verification](verification.md#linux-package-and-installer-checks).

## Developer checks

Run on Linux x64 from the repository root:

```sh
python tools/build_mod_installer.py --platform linux-x64 --output dist/input
python tools/check_linux_installer.py --game jak3 --archive dist/input/jak3-cinematic-models-linux-x64.tar.gz --output dist/verification/linux-installer.json
```

The second command checks the final package and writes a receipt. Its install and update fixtures use disposable synthetic files. See [building](building.md) for local conversion and release assembly.
