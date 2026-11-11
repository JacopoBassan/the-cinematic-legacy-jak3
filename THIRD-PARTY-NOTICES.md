# Third-party notices

Original mod code uses the ISC license in [MOD-LICENSE](MOD-LICENSE). Source derived from OpenGOAL retains its ISC notice in [builder/licenses/OpenGOAL-ISC.txt](builder/licenses/OpenGOAL-ISC.txt).

Launcher downloads include Python, NumPy, SciPy, Pillow and Zstandard. Their license files are retained with the bundled distributions. The release packager copies the pinned distributions' recorded files, including their license notices.

Windows and Apple Silicon candidates use [Astral's python-build-standalone](https://github.com/astral-sh/python-build-standalone)
CPython distributions. Their versions, source URLs and archive hashes are recorded
in `builder/platforms/windows-x64.json` and `builder/platforms/macos-arm64.json`.
Runtime and dependency license files remain in the installer bundle.

The code license does not cover game assets generated from a user's installation or the game screenshots used for comparisons and thumbnails. Generated models and textures are excluded from public downloads; comparison and thumbnail imagery is included.
