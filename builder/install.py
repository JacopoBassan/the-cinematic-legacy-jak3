"""Update a launcher-installed mod with a locally built payload."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
import platform_tools
from urllib.parse import quote
from runtime_utils import ROOT,sha
import game_config as games

METADATA_FILES = ('_metadata.json', 'cinematic-release.json', 'thumbnail.png', 'CREDITS.txt')
_LOCK_OWNERS = {}


@contextmanager
def mod_lock(mod):
    """Serialize launcher and direct updates; interrupted locks require inspection."""
    mod = Path(mod).absolute()
    reject_reparse(mod)
    key = os.path.normcase(str(mod.resolve()))
    owner = (os.getpid(), threading.get_ident())
    if _LOCK_OWNERS.get(key) == owner:
        yield
        return
    lock = mod/'.cinematic-install.lock'
    recovery = mod/'.cinematic-transaction.json'
    reject_reparse(lock)
    reject_reparse(recovery)
    if recovery.exists():
        raise ValueError(f'An interrupted update requires recovery. Preserve {recovery} and its backup before retrying.')
    token = json.dumps({'pid': owner[0], 'token': uuid.uuid4().hex})
    try:
        with lock.open('x', encoding='utf-8') as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise ValueError('Another install is running, or an interrupted install requires recovery. '
                         'Do not remove the install lock until its owner and any incomplete update have been checked.') from None
    _LOCK_OWNERS[key] = owner
    try:
        # A prior owner could finish writing a recovery marker while we were
        # attempting acquisition. Inspect it again under the exclusive lock.
        if recovery.exists():
            raise ValueError(f'An interrupted update requires recovery. Preserve {recovery} and its backup before retrying.')
        yield
    finally:
        _LOCK_OWNERS.pop(key, None)
        # Never remove a lock replaced by another actor.
        if lock.is_file() and not lock.is_symlink() and lock.read_text(encoding='utf-8') == token:
            lock.unlink()


def _remove_temporary(path):
    original_error = sys.exception()
    try:
        path.unlink(missing_ok=True)
    except BaseException as cleanup_error:
        if original_error is not None:
            raise BaseExceptionGroup(f'File operation failed ({original_error}); temporary cleanup also failed '
                                     f'for {path} ({cleanup_error}).', [original_error, cleanup_error]) from None
        raise


def atomic_copy(source, target, expected_sha):
    """Verify a sibling copy before replacing a destination, including hardlinks."""
    source, target = Path(source), Path(target)
    reject_reparse(target)
    descriptor, name = tempfile.mkstemp(prefix='', dir=target.parent)
    temp = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as outgoing:
            descriptor = None
            with source.open('rb') as incoming:
                shutil.copyfileobj(incoming, outgoing)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if sha(temp) != expected_sha:
            raise ValueError(f'Staged file checksum mismatch: {target}')
        shutil.copystat(source, temp)
        reject_reparse(target)
        os.replace(temp, target)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        _remove_temporary(temp)


def atomic_bytes(target, data):
    target = Path(target)
    reject_reparse(target)
    descriptor, name = tempfile.mkstemp(prefix='', dir=target.parent)
    temp = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            descriptor = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if sha(temp) != hashlib.sha256(data).hexdigest():
            raise ValueError(f'Metadata checksum mismatch: {target}')
        reject_reparse(target)
        os.replace(temp, target)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        _remove_temporary(temp)


def verify_payload(payload,game_config=None):
    manifest = json.loads((payload/'manifest.json').read_text())
    selected = games.validate(game_config) if game_config is not None else games.load(ROOT)
    game = selected['game']
    config = games.compatibility(ROOT,selected)
    runtime_name = platform_tools.tool_path(Path(), 'gk', config).name
    if manifest.get('schemaVersion') != 1 or manifest.get('runtimeSha256') != config['tools'][runtime_name]:
        raise ValueError('Unsupported payload manifest')
    if manifest.get('game') != game:
        raise ValueError('Payload belongs to a different game')
    for key in ('version','openGoalVersion','platform'):
        if manifest.get(key) != config[key]:
            raise ValueError(f'Unsupported payload {key}')
    seen = set()
    for entry in manifest['files']:
        name = entry['path']
        ordinary_asset = re.fullmatch(r'data/out/'+game+r'/(?:fr3/[A-Za-z0-9_]+\.fr3|iso/[A-Za-z0-9_]+\.(?:CGO|DGO))',name)
        if not ordinary_asset and name != 'data/game/graphics/opengl_renderer/shaders/merc2.vert':
            raise ValueError(f'Unsafe or unsupported payload path: {name}')
        if name.casefold() in seen:
            raise ValueError('Duplicate payload file')
        seen.add(name.casefold())
        path = payload/PurePosixPath(name)
        if not path.is_file() or path.stat().st_size != entry['bytes'] or sha(path) != entry['sha256']:
            raise ValueError(f'Payload checksum failed: {name}')
    if not {f'data/out/{game}/fr3/game.fr3',f'data/out/{game}/iso/game.cgo'}.issubset(seen):
        raise ValueError('Core mod files are missing')
    if seen != {name.casefold() for name in config['payloadPaths']}:
        raise ValueError('Payload file set does not match this release')
    return manifest


def reject_reparse(path):
    for parent in (path,*path.parents):
        if parent.is_symlink() or (parent.exists() and getattr(parent.lstat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('Install through real directories, not links or junctions')


def verify_mod_path(mod,game_config=None):
    selected = games.validate(game_config) if game_config is not None else games.load(ROOT)
    game = selected['game']
    parts = [p.lower() for p in mod.parts]
    if len(parts)<5 or parts[-5:-2] != ['features',game,'mods']:
        raise ValueError(f'Destination must be <OpenGOAL>/features/{game}/mods/<source>/<mod name>')
    if (parts[-2].startswith('_') and parts[-2] != '_local') or parts[-1].startswith('_'):
        raise ValueError('Reserved launcher folder name')
    # A reparse point would make the destination and rollback boundary ambiguous.
    reject_reparse(mod)


def require_game_closed():
    if sys.platform == 'darwin':
        result = subprocess.run(['/bin/ps', '-axo', 'comm='], capture_output=True, text=True)
        if result.returncode or not result.stdout.strip():
            raise ValueError('Could not verify that OpenGOAL is closed')
        if any(Path(line.strip()).name == 'gk' for line in result.stdout.splitlines()):
            raise ValueError('Close the running OpenGOAL game before installing')
        return
    if os.name != 'nt':
        raise ValueError('This release supports Windows and macOS only')
    command = "@(Get-CimInstance Win32_Process -Filter \"Name = 'gk.exe'\" -ErrorAction Stop).Count"
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],capture_output=True,text=True)
    if result.returncode or not result.stdout.strip().isdigit():
        raise ValueError('Could not verify that OpenGOAL is closed')
    if int(result.stdout.strip()):
        raise ValueError('Close the running OpenGOAL game before installing')


def metadata(mod,game_config=None,previous=None):
    selected = games.validate(game_config) if game_config is not None else games.load(ROOT)
    previous = previous if isinstance(previous,dict) else {}
    prefix = 'asset://localhost/' if selected.get('platform') == 'macos-arm64' else 'https://asset.localhost/'
    image = prefix+quote(str(mod/'thumbnail.png'),safe='')
    description = selected['description']
    result = dict(previous)
    result.update(name=mod.name,source=mod.parent.name,displayName=selected['displayName'],
                  description=description,authors=['@jacopobassan'],websiteUrl='https://jacopobassan.me',
                  tags=['graphics','character-models'],supportedGames=[selected['game']],
                  installed=True,metadataOffline=True,coverArtUrl=image,thumbnailArtUrl=image,externalLink=None)
    result['versions'] = previous.get('versions',[]) if isinstance(previous.get('versions',[]),list) else []
    count = previous.get('downloadCount',0)
    result['downloadCount'] = count if type(count) is int and count >= 0 else 0
    overrides = previous.get('perGameConfig')
    result['perGameConfig'] = dict(overrides) if isinstance(overrides,dict) else None
    if result['perGameConfig'] is not None and selected['game'] in result['perGameConfig']:
        entry = result['perGameConfig'][selected['game']]
        entry = dict(entry) if isinstance(entry,dict) else {}
        entry.update(displayName=result['displayName'],description=description,
                     coverArtUrl=image,thumbnailArtUrl=image)
        result['perGameConfig'][selected['game']] = entry
    return result


def put_metadata(target,final_path,manifest,game_config=None):
    selected = games.validate(game_config) if game_config is not None else games.load(ROOT)
    # Catalog versions belong to the launcher, while our descriptive fields must
    # follow the current package. Read from the installed path during staging.
    for name in METADATA_FILES:
        reject_reparse(target/name)
    reject_reparse(final_path/'_metadata.json')
    try:
        previous = json.loads((final_path/'_metadata.json').read_text(encoding='utf-8'))
    except (FileNotFoundError,ValueError):
        previous = None
    atomic_bytes(target/'_metadata.json', (json.dumps(metadata(final_path,selected,previous),indent=2)+'\n').encode('utf-8'))
    atomic_bytes(target/'cinematic-release.json', json.dumps(manifest,indent=2).encode('utf-8'))
    for source, name in ((games.relative_file(ROOT,selected['thumbnail']), 'thumbnail.png'),
                         (ROOT/'CREDITS.txt', 'CREDITS.txt')):
        atomic_copy(source, target/name, sha(source))


def install(payload,mod,backup=None,base_data=None,tools=None,*,game_config=None,build_module=None,build_config=None):
    # Keep legacy caller arguments for updates; creation belongs to the launcher adapter.
    selected = games.validate(game_config) if game_config is not None else games.load(ROOT)
    verify_mod_path(mod,selected)
    if not mod.exists():
        archive = 'archive' if selected.get('platform') == 'macos-arm64' else 'ZIP'
        raise ValueError(f'For a new mod, import the matching launcher {archive} through OpenGOAL Launcher > '
                         'Features > Mods > Add from File. It includes the installer required for rebuilds.')
    with mod_lock(mod):
        return _install_locked(payload, mod, backup, selected, game_config)


def _install_locked(payload, mod, backup, selected, game_config):
    manifest = verify_payload(payload) if game_config is None else verify_payload(payload,selected)
    require_game_closed()
    if payload.is_relative_to(mod) or mod.is_relative_to(payload):
        raise ValueError('The payload must be outside the installed mod')
    entries = manifest['files']
    metadata_files = list(METADATA_FILES)
    runtime = platform_tools.tool_path(mod, 'gk', selected)
    if not runtime.is_file() or sha(runtime) != manifest['runtimeSha256']:
        raise ValueError('Existing mod runtime is not the supported OpenGOAL version')
    if backup is None or backup.exists():
        raise ValueError('Updating an installed mod requires a new --backup folder outside OpenGOAL')
    install_root = mod.parents[4]
    if backup.is_relative_to(install_root) or install_root.is_relative_to(backup):
        raise ValueError('Backup must be outside the OpenGOAL installation')
    if backup.is_relative_to(payload) or payload.is_relative_to(backup):
        raise ValueError('Backup and payload folders must be separate')
    names = [entry['path'] for entry in entries]+metadata_files
    for name in names:
        reject_reparse(mod/name)
    reject_reparse(backup)
    backup.mkdir(parents=True)
    records = []
    for name in names:
        source = mod/name
        record = {'path':name,'existed':source.is_file()}
        if record['existed']:
            original_hash = sha(source)
            dest = backup/name
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,dest)
            record['sha256'] = sha(dest)
            if original_hash != record['sha256']:
                raise ValueError('Backup verification failed before installation')
        records.append(record)
    atomic_bytes(backup/'rollback.json', json.dumps({'schemaVersion':1,'files':records},indent=2).encode('utf-8'))
    # Stage complete replacement files before touching any installed path.
    stage = backup/'new-files'
    stage.mkdir()
    for entry in entries:
        target = stage/entry['path']
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(payload/entry['path'],target)
    if game_config is None:put_metadata(stage,mod,manifest)
    else:put_metadata(stage,mod,manifest,selected)
    expected_hashes = {entry['path']:entry['sha256'] for entry in entries}
    expected_hashes.update({name:sha(stage/name) for name in metadata_files})
    touched = []
    marker = mod/'.cinematic-transaction.json'
    atomic_bytes(marker, json.dumps({'schemaVersion':1, 'backup':str(backup.resolve()),
                                   'mod':str(mod.resolve())},indent=2).encode('utf-8'))
    try:
        for name in names:
            target = mod/name
            target.parent.mkdir(parents=True,exist_ok=True)
            # Include the attempted target before replacement so an interrupt
            # immediately after rename cannot escape restoration bookkeeping.
            touched.append(name)
            atomic_copy(stage/name, target, expected_hashes[name])
        for entry in entries:
            if sha(mod/entry['path']) != entry['sha256']:
                raise ValueError('Installed file checksum mismatch')
    except BaseException as original_error:
        failures = []
        for name in reversed(touched):
            record = next(item for item in records if item['path']==name)
            target = mod/name
            try:
                if record['existed']:
                    atomic_copy(backup/name,target,record['sha256'])
                else:
                    reject_reparse(target)
                    target.unlink(missing_ok=True)
            except BaseException as rollback_error:
                rollback_error.add_note(f'Could not restore {target}; verified backup retained at {backup}')
                failures.append(rollback_error)
        if not failures:
            try:
                marker.unlink()
            except BaseException as cleanup_error:
                failures.append(cleanup_error)
        if failures:
            details = '; '.join(f'{type(error).__name__}: {error}' for error in [original_error, *failures])
            raise BaseExceptionGroup(f'Installation failed and rollback was incomplete. Preserve backup {backup} '
                                     f'and recovery marker {marker}. {details}', [original_error, *failures]) from None
        raise
    marker.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload',type=Path,required=True)
    parser.add_argument('--mod',type=Path,required=True,help='Existing launcher-managed mod directory')
    parser.add_argument('--backup',type=Path,help='New small backup directory outside OpenGOAL, required for updates')
    parser.add_argument('--game',choices=games.GAMES,help='Game descriptor; defaults to this package')
    args = parser.parse_args()
    selected=games.load(ROOT,args.game)
    verify_mod_path(args.mod.absolute(),selected)
    resolve = lambda p:p.resolve() if p else None
    install(resolve(args.payload),resolve(args.mod),resolve(args.backup),game_config=selected)
    print('Installed and verified. Open the mod from OpenGOAL Launcher > Mods.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Installation stopped: {exc}',file=sys.stderr)
        sys.exit(1)
