#!/bin/sh
set -eu
# Keep bundled Python independent of the launcher's runtime paths.
unset LD_LIBRARY_PATH LD_PRELOAD PYTHONHOME PYTHONPATH \
    DYLD_LIBRARY_PATH DYLD_FALLBACK_LIBRARY_PATH \
    DYLD_FRAMEWORK_PATH DYLD_FALLBACK_FRAMEWORK_PATH DYLD_INSERT_LIBRARIES \
    DYLD_VERSIONED_LIBRARY_PATH DYLD_VERSIONED_FRAMEWORK_PATH \
    DYLD_ROOT_PATH DYLD_IMAGE_SUFFIX DYLD_SHARED_REGION
export PYTHONDONTWRITEBYTECODE=1
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
python="$root/.builder/python/bin/python3.11"
if [ ! -x "$python" ] || [ ! -f "$root/.builder/source/launcher_install.py" ]; then
    echo 'Cinematic installer: Import the complete native archive through OpenGOAL.' >&2
    exit 1
fi
cd "$root"
exec "$python" -I -B -u -c '
import os, runpy, sys
from pathlib import Path
if os.getpgrp() != os.getpid():
    os.setsid()
source = Path(sys.argv.pop(1))
sys.path[:0] = [str(source), str(source / "stages")]
sys.argv[0] = str(source / "launcher_install.py")
runpy.run_path(sys.argv[0], run_name="__main__")
' "$root/.builder/source" "$@"
