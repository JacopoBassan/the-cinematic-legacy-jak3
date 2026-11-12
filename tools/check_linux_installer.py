"""Check a final Linux installer with native subprocesses and disposable data.

Failure modes: an incomplete or asset-bearing TAR, bad hashes or executable modes,
missing runtime source or native dependencies, an invalid extractor CLI, crossed
launcher games, an undetected running gk, partial publication on SIGTERM, stranded
child tools, locks or transaction files. No original game data is used.

The final archive is checked unchanged. Transaction fixtures copy its source and
replace only compatibility data with hashes of synthetic files. They call the
bundled installer in a separate native process, without import mocks. Real model
conversion, Launcher GUI integration and gameplay are outside this check.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

FORBIDDEN = {'.fr3', '.cgo', '.dgo', '.glb', '.gltf', '.go', '.iso', '.o',
             '.gc', '.asm', '.sav', '.blend'}
BOOTSTRAP = """
import sys
from pathlib import Path
source = Path(sys.argv.pop(1))
sys.path[:0] = [str(source), str(source / 'stages')]
exec(compile(sys.argv.pop(1), '<linux-installer-e2e>', 'exec'))
"""
RUNTIME_CHECK = """
import importlib.metadata as md, json, platform
import numpy, scipy.spatial, PIL.Image, zstandard
expected = json.loads(sys.argv[1])
assert sys.version_info[:3] == tuple(map(int, sys.argv[2].split('.')))
assert all(md.version(name) == version for name, version in expected.items())
assert numpy.allclose(numpy.linalg.solve([[2., 0.], [0., 4.]], [6., 8.]), [3., 2.])
distance, index = scipy.spatial.cKDTree([[0., 0.], [2., 2.]]).query([.1, .1])
assert index == 0 and distance < .2
image = PIL.Image.new('RGBA', (1, 1), (1, 2, 3, 255))
assert image.getpixel((0, 0)) == (1, 2, 3, 255)
sample = b'cinematic native Linux E2E'
assert zstandard.ZstdDecompressor().decompress(zstandard.ZstdCompressor().compress(sample)) == sample
import game_config as games, launcher_install, install, platform_tools
selected = games.load(source)
assert selected['game'] == sys.argv[3] and selected['platform'] == 'linux-x64'
assert platform_tools.host_platform() == 'linux-x64'
config = games.compatibility(source, selected)
assert config['platform'] == 'linux-x64'
assert config['tools'] and set(config['tools']) == {'extractor', 'goalc', 'gk'}
python_root = (source.parent / 'python').resolve()
libpython_origins = set()
for mapping in Path('/proc/self/maps').read_text().splitlines():
    fields = mapping.split(maxsplit=5)
    if len(fields) == 6 and 'libpython' in Path(fields[5]).name:
        origin = Path(fields[5]).resolve()
        assert origin.is_relative_to(python_root), 'Loaded libpython is outside the bundle: ' + str(origin)
        libpython_origins.add(origin.relative_to(python_root).as_posix())
module = games.builder(selected)
assert callable(module.preflight) and callable(module.build)
if hasattr(module, 'SOURCE_CLOSURE'):
    assert all((source / name).is_file() for name in module.SOURCE_CLOSURE)
print(json.dumps({'python': platform.python_version(), 'dependencies': expected,
                  'builder': selected['builderModule'], 'libpythonOrigins': sorted(libpython_origins)}))
"""
INSTALL = """
import game_config as games, install, launcher_install
launcher_install.install_signal_handlers()
selected = games.load(source)
install.install(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), game_config=selected)
print('synthetic update complete')
"""
FIRST_INSTALL = """
import game_config as games, launcher_install
launcher_install.install_signal_handlers()
selected = games.load(source)
launcher_install.install_generated(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]),
                                 Path(sys.argv[4]), Path(sys.argv[5]), selected)
print('synthetic first install complete')
"""
CANCEL_INSTALL = """
import json, subprocess
import game_config as games, install, launcher_install
launcher_install.install_signal_handlers()
child = subprocess.Popen([sys.executable, '-I', '-B', '-c', 'import time; time.sleep(120)'])
Path(sys.argv[4]).write_text(str(child.pid))
try:
    selected = games.load(source)
    install.install(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), game_config=selected)
finally:
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.terminate()
        child.wait(timeout=5)
        raise AssertionError('SIGTERM did not stop the child tool process')
"""
MARKER_CANCEL_INSTALL = """
import signal
import game_config as games, install, launcher_install
launcher_install.install_signal_handlers()
mod = Path(sys.argv[2])
ready = Path(sys.argv[4])
def pause_at_marker(frame, event, arg):
    # Pause only after the real atomic rename has published this transaction's
    # marker. No installer function or filesystem operation is replaced.
    if (event == 'line' and frame.f_code.co_name == 'atomic_bytes'
            and Path(frame.f_code.co_filename) == source / 'install.py'
            and frame.f_locals.get('target') == mod / '.cinematic-transaction.json'
            and (mod / '.cinematic-transaction.json').is_file()):
        sys.settrace(None)
        ready.write_text('published')
        signal.pause()
        raise AssertionError('Marker pause resumed without cancellation')
    return pause_at_marker
sys.settrace(pause_at_marker)
try:
    selected = games.load(source)
    install.install(Path(sys.argv[1]), mod, Path(sys.argv[3]), game_config=selected)
finally:
    sys.settrace(None)
"""
MARKER_CANCEL_FIRST_INSTALL = """
import signal
import game_config as games, launcher_install
launcher_install.install_signal_handlers()
mod = Path(sys.argv[2])
ready = Path(sys.argv[6])
foreign = sys.argv[7] == 'foreign'
def pause_at_marker(frame, event, arg):
    marker = mod / '.cinematic-transaction.json'
    atomic_publication = (frame.f_code.co_name == 'atomic_bytes'
                          and frame.f_locals.get('target') == marker)
    exclusive_creation = (frame.f_code.co_name == '_install_generated'
                          and frame.f_locals.get('transaction') == marker
                          and (not foreign or frame.f_locals.get('transaction_created')))
    if event == 'line' and (atomic_publication or exclusive_creation) and marker.is_file():
        sys.settrace(None)
        ready.write_text('published')
        signal.pause()
        raise AssertionError('First-install marker pause resumed without cancellation')
    return pause_at_marker
sys.settrace(pause_at_marker)
try:
    selected = games.load(source)
    launcher_install.install_generated(Path(sys.argv[1]), mod, Path(sys.argv[3]),
                                     Path(sys.argv[4]), Path(sys.argv[5]), selected)
finally:
    sys.settrace(None)
"""


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    require(bool(name) and name == path.as_posix() and not path.is_absolute()
            and '..' not in path.parts and '\\' not in name and ':' not in name,
            'Unsafe archive path: ' + name)
    require(path.suffix.lower() not in FORBIDDEN, 'Game asset in installer: ' + name)
    require(path.parts[0] not in {'data', 'iso_data', 'settings'},
            'Game data folder in installer: ' + name)
    return path


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): sha(p)
            for p in folder.rglob('*') if p.is_file()}


def verify_archive(archive, destination, expected_game):
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        names = [member.name for member in members]
        require(len(names) == len(set(name.casefold() for name in names)),
                'Duplicate TAR member')
        for member in members:
            safe_name(member.name)
            require(member.isfile(), 'Non-file TAR member: ' + member.name)
            require(not member.mode & 0o6000, 'Set-ID TAR member: ' + member.name)
        require('bundle-manifest.json' in names, 'Missing bundle manifest')
        manifest = json.load(bundle.extractfile('bundle-manifest.json'))
        require(manifest.get('schemaVersion') == 1 and manifest.get('platform') == 'linux-x64',
                'Expected a Linux x64 bundle manifest')
        require(manifest.get('gameAssetsIncluded') is False, 'Bundle does not exclude game assets')
        require(manifest.get('game') in {'jak1', 'jak2', 'jak3'}, 'Unsupported game')
        if expected_game:
            require(manifest['game'] == expected_game, 'Archive belongs to a different game')
        files = manifest['files']
        require(set(names) == set(files) | {'bundle-manifest.json'},
                'Archive files differ from the bundle manifest')
        for member in members:
            if member.name in files:
                with bundle.extractfile(member) as stream:
                    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
                require(actual == files[member.name], 'Archive checksum failed: ' + member.name)
        for name in ('extractor', '.builder/python/bin/python3.11'):
            require(name in files and bundle.getmember(name).mode & 0o111,
                    'Missing executable TAR mode: ' + name)
        bundle.extractall(destination, filter='data')
    source = destination/'.builder/source'
    selected = read_json(source/'game-config.json')
    game = manifest['game']
    require(selected.get('game') == game and selected.get('platform') == 'linux-x64',
            'Wrong selected game or platform descriptor')
    require(selected.get('modName') == game+'-cinematic-models-linux-x64',
            'Wrong Linux launcher mod name')
    inventory = read_json(source/'source-files.json')
    require(inventory.get('game') == game, 'Wrong source inventory game')
    runtime = inventory['runtimeFiles']
    require(len(runtime) == len(set(name.casefold() for name in runtime)),
            'Duplicate runtime source inventory path')
    for name in runtime:
        safe_name(name)
    actual_source = {p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file()}
    require(actual_source == set(runtime), 'Runtime source inventory is not closed')
    required = {'launcher_install.py', 'install.py', 'runtime_utils.py', 'game_config.py',
                'platform_tools.py', 'requirements.txt', 'source-files.json', 'game-config.json',
                'platforms/linux-x64.json', selected['compatibilityFile'],
                selected['builderModule'].replace('.', '/')+'.py'}
    require(required.issubset(actual_source), 'Missing required Linux runtime sources')
    for name in actual_source:
        parts = PurePosixPath(name).parts
        require(not (parts[0] in {'jak1', 'jak2', 'jak3'} and parts[0] != game),
                'Another game runtime source is bundled: ' + name)
        require(parts[0] != 'game-configs' or name == f'game-configs/{game}.json',
                'Another game descriptor is bundled: ' + name)
        require(parts[0] != 'artwork' or name == selected['thumbnail'],
                'Another game artwork is bundled: ' + name)
        if name.endswith('.py'):
            ast.parse((source/name).read_bytes(), filename=name)
    requirements = dict(line.split('==') for line in (source/'requirements.txt').read_text().splitlines()
                        if line.strip() and not line.startswith('#'))
    require(manifest['dependencies'] == requirements, 'Dependency pins differ from the bundle source')
    profile = read_json(source/'platforms/linux-x64.json')
    require(manifest['pythonRuntime'] == profile['pythonRuntime'], 'Python runtime pin mismatch')
    python = destination/'.builder/python/bin/python3.11'
    with python.open('rb') as stream:
        header = stream.read(20)
    require(header[:5] == b'\x7fELF\x02' and header[5] == 1
            and int.from_bytes(header[18:20], 'little') == 62,
            'Bundled Python is not a Linux x64 ELF executable')
    return manifest, selected, requirements


def command(python, source, code, *args):
    return [str(python), '-I', '-B', '-u', '-c', BOOTSTRAP, str(source), code, *map(str, args)]


def run(args, env, *, success=True, contains=None):
    result = subprocess.run(args, cwd=env['HOME'], env=env, capture_output=True, text=True, timeout=60)
    combined = result.stdout + result.stderr
    require((result.returncode == 0) == success,
            f'Unexpected process exit {result.returncode}: {combined[-4000:]}')
    if contains:
        require(contains.lower() in combined.lower(), f'Missing diagnostic {contains!r}: {combined[-4000:]}')
    return result


class Fixture:
    """Only authored bytes in a new temporary launcher layout."""
    def __init__(self, root, package, selected, *, large=False):
        self.root, self.game = root, selected['game']
        native = selected.get('platform', 'linux-x64')
        self.source = root/'source'
        shutil.copytree(package/'.builder/source', self.source)
        self.mod = root/'OpenGOAL/features'/self.game/'mods/fixture/Synthetic Models'
        self.mod.mkdir(parents=True)
        self.payload, self.backup = root/'payload', root/'previous-release'
        self.data = root/'OpenGOAL/active'/self.game/'data'
        self.tools = root/'OpenGOAL/versions/official/fixture-only'
        self.tools.mkdir(parents=True)
        runtime = b'Synthetic runtime, never executed\n'
        for name in ('gk', 'goalc', 'extractor'):
            (self.tools/name).write_bytes(runtime + name.encode())
            (self.tools/name).chmod(0o755)
        shutil.copy2(self.tools/'gk', self.mod/'gk')
        (self.mod/'settings/saves').mkdir(parents=True)
        (self.mod/'settings/saves/slot.bin').write_bytes(b'Synthetic saved progress\n')
        self.entries = []
        names = [f'data/out/{self.game}/fr3/GAME.fr3', f'data/out/{self.game}/iso/GAME.CGO',
                 'data/game/graphics/opengl_renderer/shaders/merc2.vert']
        for index, name in enumerate(names):
            target = self.payload/name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('wb') as stream:
                stream.write(('Synthetic payload '+name+'\n').encode())
                if large and index == 1:
                    block = b'x' * (1024 * 1024)
                    for _ in range(64):
                        stream.write(block)
            self.entries.append(dict(path=name, bytes=target.stat().st_size, sha256=sha(target)))
            old = self.mod/name
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_bytes(('Synthetic previous '+name+'\n').encode())
        for name in ('_metadata.json', 'thumbnail.png', 'CREDITS.txt', 'cinematic-release.json'):
            (self.mod/name).write_bytes(('Synthetic previous '+name+'\n').encode())
        self.config = dict(game=self.game, version='fixture-only', openGoalVersion='fixture-only',
                           platform=native, tools={p.name: sha(p) for p in self.tools.iterdir()},
                           payloadPaths=names)
        # Use the platform selector unchanged, with a synthetic local profile.
        profile_path = self.source/'platforms'/f'{native}.json'
        profile = read_json(profile_path)
        profile.update(self.config)
        profile.pop('gameProfiles', None)
        profile_path.write_text(json.dumps(profile))
        (self.source/selected['compatibilityFile']).write_text(json.dumps(self.config))
        self.manifest = dict(self.config, schemaVersion=1, runtimeSha256=sha(self.mod/'gk'), files=self.entries)
        self.save_manifest()

    def save_manifest(self):
        (self.payload/'manifest.json').write_text(json.dumps(self.manifest))

    def args(self, python, code=INSTALL, *extra):
        return command(python, self.source, code, self.payload, self.mod, self.backup, *extra)


def no_transaction(fixture):
    for name in ('.cinematic-install.lock', '.cinematic-transaction.json'):
        require(not (fixture.mod/name).exists(), 'Installer left ' + name)


def check_first_install(root, package, python, selected, env):
    fixture = Fixture(root/'first-install', package, selected)
    # The package's launcher-owned source and the synthetic save remain in place.
    # Remove only this fixture's prior generated runtime/data/metadata.
    shutil.rmtree(fixture.mod/'data')
    for name in ('gk', '_metadata.json', 'thumbnail.png', 'CREDITS.txt', 'cinematic-release.json'):
        (fixture.mod/name).unlink()
    old = snapshot(fixture.mod)
    for folder in selected['runtimeFolders']:
        target = fixture.data/folder/'synthetic-input.txt'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(('Synthetic original '+folder+'\n').encode())
    originals = snapshot(fixture.data)
    job = fixture.root/'job'; job.mkdir()
    run(command(python, fixture.source, FIRST_INSTALL, fixture.payload, fixture.mod,
                fixture.data, fixture.tools, job), env)
    for entry in fixture.entries:
        require(sha(fixture.mod/entry['path']) == entry['sha256'], 'First-install payload mismatch')
    require(sha(fixture.mod/'gk') == fixture.config['tools']['gk'], 'First-install runtime mismatch')
    require((fixture.mod/'goalc').is_file(), 'First install did not publish the compiler')
    require(all((fixture.mod/name).stat().st_mode & 0o111 for name in ('gk', 'goalc')),
            'First install lost native tool executable modes')
    require(sha(fixture.mod/'settings/saves/slot.bin') == old['settings/saves/slot.bin'],
            'First install changed the synthetic save')
    require(snapshot(fixture.data) == originals, 'First install changed original input data')
    require(not (job/'installation').exists(), 'First install retained its staging folder')
    no_transaction(fixture)


def check_update(root, package, python, selected, env):
    fixture = Fixture(root/'update', package, selected)
    old = snapshot(fixture.mod)
    run(fixture.args(python), env)
    for entry in fixture.entries:
        require(sha(fixture.mod/entry['path']) == entry['sha256'], 'Published payload mismatch')
    for name in ('gk', 'settings/saves/slot.bin'):
        require(sha(fixture.mod/name) == old[name], 'Update changed runtime or save')
    records = read_json(fixture.backup/'rollback.json')['files']
    for record in records:
        if record['existed']:
            require(sha(fixture.backup/record['path']) == old[record['path']], 'Backup mismatch')
    metadata = read_json(fixture.mod/'_metadata.json')
    require(metadata['supportedGames'] == [selected['game']], 'Wrong game in installed metadata')
    require(metadata['coverArtUrl'].startswith('asset://localhost/'), 'Wrong POSIX launcher asset URL')
    no_transaction(fixture)


def check_rejections(root, package, python, selected, env):
    fixture = Fixture(root/'rejections', package, selected)
    other = 'jak2' if selected['game'] == 'jak1' else 'jak1'
    before = snapshot(fixture.mod)
    fixture.manifest['game'] = other
    fixture.save_manifest()
    run(fixture.args(python), env, success=False, contains='different game')
    require(snapshot(fixture.mod) == before and not fixture.backup.exists(),
            'Wrong-game payload changed installed data')
    no_transaction(fixture)
    fixture.manifest['game'] = selected['game']
    fixture.save_manifest()
    wrong_mod = root/'OpenGOAL/features'/other/'mods/fixture/Wrong Game'
    wrong_mod.mkdir(parents=True)
    run(command(python, fixture.source, INSTALL, fixture.payload, wrong_mod, fixture.backup),
        env, success=False, contains='Destination')
    require(not list(wrong_mod.iterdir()) and not fixture.backup.exists(), 'Wrong-game path was changed')
    # A real native process named gk must prevent an otherwise valid update.
    running = root/'gk'
    shutil.copyfile('/bin/sleep', running)
    running.chmod(0o755)
    process = subprocess.Popen([str(running), '120'], env=env)
    try:
        require(process.poll() is None, 'Native running-game fixture did not start')
        run(fixture.args(python), env, success=False, contains='Close the running OpenGOAL game')
        require(snapshot(fixture.mod) == before and not fixture.backup.exists(),
                'Running-game refusal changed installed data')
        no_transaction(fixture)
    finally:
        process.terminate()
        process.wait(timeout=5)
    # Retry proves that a refusal did not strand the exclusive install lock.
    run(fixture.args(python), env)
    no_transaction(fixture)


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def check_marker_cancellation(root, package, python, selected, env, *, foreign=False, first_install=False):
    name = 'foreign-marker-cancel' if foreign else 'marker-cancel'
    if first_install:
        name = 'first-'+name
    fixture = Fixture(root/name, package, selected)
    if first_install:
        shutil.rmtree(fixture.mod/'data')
        for existing in ('gk', '_metadata.json', 'thumbnail.png', 'CREDITS.txt', 'cinematic-release.json'):
            (fixture.mod/existing).unlink()
        for folder in selected['runtimeFolders']:
            target = fixture.data/folder/'synthetic-input.txt'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(('Synthetic original '+folder+'\n').encode())
        job = fixture.root/'job'; job.mkdir()
    before = snapshot(fixture.mod)
    ready = fixture.root/'marker-ready'
    log = fixture.root/'cancel.log'
    marker = fixture.mod/'.cinematic-transaction.json'
    foreign_data = b'Synthetic recovery marker owned by another actor\n'
    if first_install:
        args = command(python, fixture.source, MARKER_CANCEL_FIRST_INSTALL, fixture.payload,
                       fixture.mod, fixture.data, fixture.tools, job, ready,
                       'foreign' if foreign else 'own')
    else:
        args = fixture.args(python, MARKER_CANCEL_INSTALL, ready)
    with log.open('w+') as stream:
        process = subprocess.Popen(args,
                                   cwd=env['HOME'], env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 20
            while not ready.is_file():
                require(process.poll() is None, 'Marker cancellation fixture exited before publication')
                require(time.monotonic() < deadline, 'No marker publication reached within 20 seconds')
                time.sleep(.001)
            require(marker.is_file(), 'Marker pause did not follow real file publication')
            if foreign:
                replacement = fixture.root/'foreign-marker'
                replacement.write_bytes(foreign_data)
                os.replace(replacement, marker)
            process.send_signal(signal.SIGTERM)
            code = process.wait(timeout=20)
            stream.seek(0)
            output = stream.read()
            require(code != 0 and 'Installation cancelled' in output,
                    f'SIGTERM did not cancel marker publication: {output[-4000:]}')
            expected = dict(before)
            if foreign:
                expected[marker.name] = hashlib.sha256(foreign_data).hexdigest()
                require(marker.is_file() and marker.read_bytes() == foreign_data,
                        'Cancellation removed or changed another actor\'s recovery marker')
                require(not (fixture.mod/'.cinematic-install.lock').exists(), 'Installer left its lock')
            else:
                no_transaction(fixture)
            require(snapshot(fixture.mod) == expected, 'Marker-publication cancellation changed installed data')
            if first_install:
                require((job/'installation').exists() == foreign,
                        'First-install staging did not follow marker ownership')
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
    fixture.backup = fixture.root/'retry-backup'
    retry = (command(python, fixture.source, FIRST_INSTALL, fixture.payload, fixture.mod,
                     fixture.data, fixture.tools, job) if first_install else fixture.args(python))
    if foreign:
        run(retry, env, success=False, contains='recovery')
        require(not fixture.backup.exists() and snapshot(fixture.mod) == expected,
                'Retry bypassed another actor\'s recovery marker')
    else:
        run(retry, env)
        no_transaction(fixture)


def check_cancellation(root, package, python, selected, env):
    fixture = Fixture(root/'cancel', package, selected, large=True)
    before = snapshot(fixture.mod)
    child_pid_file = root/'cancel-child.pid'
    log = root/'cancel.log'
    child_pid = None
    with log.open('w+') as stream:
        process = subprocess.Popen(fixture.args(python, CANCEL_INSTALL, child_pid_file),
                                   cwd=env['HOME'], env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            # A small first file followed by a large second file gives the real
            # atomic-copy transaction a bounded, observable publication window.
            first = fixture.mod/fixture.entries[0]['path']
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if child_pid_file.exists():
                    child_text = child_pid_file.read_text().strip()
                    if child_text:
                        child_pid = int(child_text)
                if first.read_bytes() == (fixture.payload/fixture.entries[0]['path']).read_bytes():
                    require((fixture.mod/'.cinematic-transaction.json').is_file(),
                            'Transaction finished before cancellation could be tested')
                    process.send_signal(signal.SIGTERM)
                    break
                require(process.poll() is None, 'Cancellation fixture exited before publication')
                time.sleep(.001)
            else:
                raise AssertionError('No publication reached within 30 seconds')
            code = process.wait(timeout=20)
            stream.seek(0)
            output = stream.read()
            require(code != 0 and 'Installation cancelled' in output,
                    f'SIGTERM did not reach installer cancellation: {output[-4000:]}')
            require('SIGTERM did not stop the child' not in output, 'Child process survived installer cancellation')
            require(child_pid is not None and not process_alive(child_pid), 'Cancelled child is still running')
            require(snapshot(fixture.mod) == before, 'SIGTERM left a partial installation or changed saves')
            no_transaction(fixture)
            records = read_json(fixture.backup/'rollback.json')['files']
            require(all(not record['existed'] or sha(fixture.backup/record['path']) == before[record['path']]
                        for record in records), 'Cancellation damaged the recovery backup')
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            if child_pid and process_alive(child_pid):
                os.kill(child_pid, signal.SIGKILL)
    # A fresh update must succeed after rollback without removing any lock.
    fixture.backup = fixture.root/'retry-backup'
    run(fixture.args(python), env)
    no_transaction(fixture)


def check_cli(package, python, selected, env):
    extractor = package/'extractor'
    help_result = run([str(extractor), '--help'], env)
    for option in ('--folder', '--game', '--decompile', '--compile'):
        require(option in help_result.stdout, 'Extractor CLI is missing ' + option)
    other = 'jak2' if selected['game'] == 'jak1' else 'jak1'
    run([str(extractor), '/no-real-game-data', '--folder', '--game', other, '--decompile'],
        env, success=False, contains='invalid choice')
    run([str(extractor), '/no-real-game-data', '--game', selected['game'], '--decompile'],
        env, success=False, contains='Install the vanilla game')
    run([str(extractor), '/no-real-game-data', '--folder', '--game', selected['game']],
        env, success=False, contains='required')
    run([str(extractor), '/no-real-game-data', '--folder', '--game', selected['game'], '--decompile', '--compile'],
        env, success=False, contains='not allowed')


def check_extractor_environment(root, package, env):
    # The final shell and Python stay unchanged. A disposable launcher module
    # reports the environment after the real shell has executed bundled Python.
    probe = root/'extractor-environment'
    source = probe/'.builder/source'; source.mkdir(parents=True)
    (probe/'.builder/python').symlink_to(package/'.builder/python', target_is_directory=True)
    shutil.copy2(package/'extractor', probe/'extractor')
    keys = ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONHOME', 'PYTHONPATH',
            'DYLD_LIBRARY_PATH', 'DYLD_FALLBACK_LIBRARY_PATH', 'DYLD_FRAMEWORK_PATH',
            'DYLD_FALLBACK_FRAMEWORK_PATH', 'DYLD_INSERT_LIBRARIES')
    (source/'launcher_install.py').write_text(
        'import json, os\nkeys = '+repr(keys)+'\n'
        'print(json.dumps({key: os.environ[key] for key in keys if key in os.environ}))\n')
    contaminated = {**env, **dict.fromkeys(keys, str(probe/'foreign-libraries'))}
    # Empty preload values test removal without loading any injected library.
    contaminated['LD_PRELOAD'] = contaminated['DYLD_INSERT_LIBRARIES'] = ''
    result = run([str(probe/'extractor'), '--help'], contaminated)
    inherited = json.loads(result.stdout)
    require(not inherited, 'Extractor inherited loader/Python variables: '+', '.join(sorted(inherited)))


def check(archive, output, expected_game=None):
    archive = archive.resolve()
    require(archive != output.resolve(), 'The receipt must not replace the archive')
    receipt = dict(schemaVersion=1, platform='linux-x64', archive=archive.name,
                   gameAssetsUsed=False, checks=[],
                   syntheticCompatibilityFixtures=True,
                   reproduce=['python', 'tools/check_linux_installer.py', '--archive', archive.name,
                              '--output', output.name],
                   expected='All checks pass; original inputs, runtime and saves remain unchanged',
                   untested=['real-model-conversion', 'launcher-gui', 'gameplay'])
    started = time.monotonic()
    try:
        require(sys.platform == 'linux' and platform.machine().lower() in {'x86_64', 'amd64'},
                'Run the native installer check on Linux x64; other hosts cannot verify this archive')
        receipt['archiveSha256'] = sha(archive)
        # This task owns and removes only this exact temporary workspace.
        with tempfile.TemporaryDirectory(prefix='cinematic-linux-e2e-') as temporary:
            root = Path(temporary).resolve()
            package = root/'archive'; package.mkdir()
            manifest, selected, requirements = verify_archive(archive, package, expected_game)
            receipt.update(game=manifest['game'], files=len(manifest['files']))
            receipt['checks'].append('tar-hashes-modes-source-closure-no-assets')
            home = root/'home'; home.mkdir()
            env = {**os.environ, 'HOME': str(home), 'TMPDIR': str(home),
                   'XDG_CONFIG_HOME': str(home/'config'), 'XDG_DATA_HOME': str(home/'data'),
                   'XDG_CACHE_HOME': str(home/'cache'), 'PYTHONDONTWRITEBYTECODE': '1',
                   'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
            for name in tuple(env):
                if name in ('LD_LIBRARY_PATH', 'LD_PRELOAD') or name.startswith('DYLD_'):
                    env.pop(name)
            python = package/'.builder/python/bin/python3.11'
            result = run(command(python, package/'.builder/source', RUNTIME_CHECK,
                                 json.dumps(requirements), manifest['pythonRuntime']['version'], selected['game']), env)
            receipt['runtime'] = json.loads(result.stdout)
            receipt['checks'].append('isolated-bundled-native-dependency-operations')
            check_cli(package, python, selected, env)
            receipt['checks'].append('extractor-cli-contract')
            check_extractor_environment(root, package, env)
            receipt['checks'].append('extractor-clears-inherited-loader-and-python-environment')
            check_first_install(root, package, python, selected, env)
            receipt['checks'].append('synthetic-first-install-input-and-save-preservation')
            check_update(root, package, python, selected, env)
            receipt['checks'].append('synthetic-update-backup-save-preservation')
            check_rejections(root, package, python, selected, env)
            receipt['checks'].append('wrong-game-and-native-running-gk-refusal')
            check_cancellation(root, package, python, selected, env)
            receipt['checks'].append('native-sigterm-child-stop-rollback-cleanup-retry')
            check_marker_cancellation(root, package, python, selected, env)
            check_marker_cancellation(root, package, python, selected, env, foreign=True)
            receipt['checks'].append('native-sigterm-marker-cleanup-and-foreign-marker-preservation')
            check_marker_cancellation(root, package, python, selected, env, first_install=True)
            check_marker_cancellation(root, package, python, selected, env, first_install=True, foreign=True)
            receipt['checks'].append('first-install-sigterm-marker-cleanup-and-foreign-marker-preservation')
        receipt['status'] = 'passed'
    except BaseException as error:
        receipt.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        receipt['seconds'] = round(time.monotonic() - started, 2)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(receipt))
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True, help='Final asset-free Linux TAR.GZ installer')
    parser.add_argument('--output', type=Path, required=True, help='JSON verification receipt')
    parser.add_argument('--game', choices=('jak1', 'jak2', 'jak3'), help='Expected archive game')
    args = parser.parse_args()
    try:
        check(args.archive, args.output.resolve(), args.game)
    except (Exception, KeyboardInterrupt) as error:
        print(f'Linux installer check failed: {error}', file=sys.stderr)
        sys.exit(1)
