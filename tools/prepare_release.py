"""Verify this game's asset-free installers and source archive."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import runpy
import stat
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MOD = json.loads((ROOT/'mod-release.json').read_text())
GAME = MOD['game']
GAMES = {GAME: MOD['displayName']}
FORBIDDEN = {'.fr3', '.cgo', '.dgo', '.glb', '.gltf', '.go', '.iso', '.o', '.gc', '.asm', '.sav', '.blend'}
PLATFORMS = {'windows-x64': ('windows', 'zip'), 'macos-arm64': ('macos', 'tar.gz')}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def safe_name(name, *, source=False):
    path = PurePosixPath(name)
    forbidden = FORBIDDEN | ({'.exe', '.dll', '.pyd', '.dylib', '.so'} if source else set())
    if (not name or path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name
            or name != path.as_posix() or any(ord(c) < 32 for c in name)
            or path.suffix.lower() in forbidden):
        raise ValueError(f'Unexpected public file: {name}')
    if any(part.rstrip(' .') != part for part in path.parts):
        raise ValueError(f'Ambiguous archive path: {name}')
    if path.name.lower() in {'gk', 'gk.exe', 'goalc', 'goalc.exe', 'decompiler', 'decompiler.exe'}:
        raise ValueError(f'Game runtime/data in public installer: {name}')
    return path


def _sources(attribute):
    """Read an explicit file set without importing the game builders."""
    names = runpy.run_path(str(ROOT/'builder/package_source.py'))[attribute]
    if len(names) != len(set(name.casefold() for name in names)):
        raise ValueError('Duplicate source entry')
    result = {}
    for name in names:
        safe_name(name, source=True)
        path = ROOT/'builder'/name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or linked reviewed source: '+name)
        result[name] = path.read_bytes()
    return result


def source_files():
    return _sources('FILES')


def runtime_files():
    return _sources('RUNTIME_FILES')


def selected_sources(game, platform):
    if game != GAME or platform not in PLATFORMS:
        raise ValueError('Unsupported game or platform')
    sources = runtime_files()
    selected = json.loads(sources[f'game-configs/{game}.json'])
    if platform == 'macos-arm64':
        selected.update(platform=platform, modName=game + '-cinematic-models-macos-arm64',
                        installGuide='LAUNCHER-INSTALL-MAC.txt')
    sources['game-config.json'] = (json.dumps(selected, indent=2) + '\n').encode()
    return sources, selected


def checked_manifest(manifest, actual, *, source=False):
    records = manifest.get('files')
    manifest_name = 'source-manifest.json' if source else 'bundle-manifest.json'
    if not isinstance(records, dict) or set(actual) != set(records) | {manifest_name}:
        raise ValueError('Unexpected files in archive manifest')
    for name, digest in records.items():
        safe_name(name, source=source)
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or actual[name] != digest:
            raise ValueError(f'Checksum mismatch: {name}')


def check_installer_name(name, platform):
    safe_name(name)
    top = {'extractor.exe' if platform == 'windows-x64' else 'extractor',
           'thumbnail.png', 'CREDITS.txt', 'INSTALL.txt', 'bundle-manifest.json'}
    if name not in top and not name.startswith(('.builder/source/', '.builder/python/')):
        raise ValueError(f'Unexpected installer resource: {name}')


def read_archive(path, platform=None):
    """Hash members without extraction; TAR is read once in archive order."""
    actual, modes, manifest = {}, {}, None
    manifest_name = 'bundle-manifest.json' if platform else 'source-manifest.json'
    names = set()

    def consume(name, source, mode):
        nonlocal manifest
        if platform:
            check_installer_name(name, platform)
        else:
            safe_name(name, source=True)
        if name.casefold() in names:
            raise ValueError(f'Duplicate archive entry: {name}')
        names.add(name.casefold())
        modes[name] = mode
        if name == manifest_name:
            data = source.read()
            manifest = json.loads(data)
            actual[name] = sha(data)
        else:
            actual[name] = hashlib.file_digest(source, 'sha256').hexdigest()

    if platform == 'macos-arm64':
        with tarfile.open(path, 'r|gz') as archive:
            for member in archive:
                if not member.isfile() or member.mode & 0o7000:
                    raise ValueError(f'Unexpected non-file or privileged TAR entry: {member.name}')
                with archive.extractfile(member) as source:
                    consume(member.name, source, member.mode)
    else:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                mode = member.external_attr >> 16
                if member.is_dir() or (stat.S_IFMT(mode) and not stat.S_ISREG(mode)):
                    raise ValueError(f'Unexpected non-file ZIP entry: {member.filename}')
                with archive.open(member) as source:
                    consume(member.filename, source, mode)
    if not isinstance(manifest, dict):
        raise ValueError(f'Missing {manifest_name}')
    checked_manifest(manifest, actual, source=platform is None)
    return manifest, actual, modes


def verify_bundle(path, game, version, platform='windows-x64'):
    if game not in GAMES or platform not in PLATFORMS:
        raise ValueError('Unsupported game or platform')
    manifest, actual, modes = read_archive(path, platform)
    if (manifest.get('schemaVersion') != 1 or manifest.get('gameAssetsIncluded') is not False
            or manifest.get('version') != version or manifest.get('platform') != platform):
        raise ValueError(f'Incorrect manifest in {path.name}')
    if manifest.get('game') != game:
        raise ValueError(f'Wrong game descriptor: {path.name}')
    sources, selected = selected_sources(game, platform)
    expected = {'.builder/source/' + name: sha(data) for name, data in sources.items()}
    bundled = {name: digest for name, digest in actual.items() if name.startswith('.builder/source/')}
    if bundled != expected:
        raise ValueError(f'Source closure differs from reviewed checkout: {path.name}')
    requirements = dict(line.split('==') for line in sources['requirements.txt'].decode().splitlines()
                        if line.strip() and not line.startswith('#'))
    compatibility = json.loads(sources[selected['compatibilityFile']])
    if manifest.get('dependencies') != requirements or manifest.get('openGoalVersion') != compatibility['openGoalVersion']:
        raise ValueError(f'Incorrect pinned dependencies/tooling: {path.name}')
    resources = source_files()
    for name, source in (('thumbnail.png', selected['thumbnail']), ('CREDITS.txt', 'CREDITS.txt'),
                         ('INSTALL.txt', selected['installGuide'])):
        if actual.get(name) != sha(resources[source]):
            raise ValueError(f'Incorrect installer resource: {name}')
    python = '.builder/python/python.exe' if platform == 'windows-x64' else '.builder/python/bin/python3.11'
    if python not in actual:
        raise ValueError('Missing portable Python runtime')
    if platform == 'macos-arm64':
        config = json.loads(sources['platforms/macos-arm64.json'])
        if manifest.get('pythonRuntime') != config['pythonRuntime'] or manifest['openGoalVersion'] != config['openGoalVersion']:
            raise ValueError('Incorrect pinned Mac runtime/tooling')
        if actual.get('extractor') != sha(resources['LauncherExtractor.sh']):
            raise ValueError('Incorrect Mac launcher adapter')
        if any(not modes[name] & stat.S_IXUSR for name in ('extractor', python)):
            raise ValueError('Mac archive lost executable permissions')
    elif 'extractor.exe' not in actual:
        raise ValueError('Missing Windows launcher adapter')
    elif manifest.get('pythonRuntime') != json.loads(resources['platforms/windows-x64.json'])['pythonRuntime']:
        raise ValueError('Incorrect pinned Windows Python runtime')
    if platform == 'windows-x64' and manifest.get('adapterSourceSha256') != sha(resources['LauncherExtractor.cs']):
        raise ValueError('Incorrect Windows adapter source')
    return {'filename': path.name, 'bytes': path.stat().st_size, 'sha256': file_sha(path),
            'game': game, 'platform': platform, 'sourceFiles': len(sources)}


def verify_source(path):
    manifest, actual, _ = read_archive(path)
    if (manifest.get('schemaVersion') != 1 or manifest.get('assetFreeSource') is not True
            or manifest.get('game') != GAME):
        raise ValueError('Invalid source archive')
    sources = source_files()
    expected = {name: sha(data) for name, data in sources.items()}
    if manifest['files'] != expected:
        raise ValueError('Source archive differs from reviewed checkout')
    return {'filename': path.name, 'bytes': path.stat().st_size, 'sha256': file_sha(path),
            'kind': 'source', 'sourceFiles': len(sources)}
