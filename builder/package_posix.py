"""Package an asset-free native POSIX installer with a pinned Python runtime."""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import sysconfig
import tarfile

import game_config as games
from package_launcher import dependency_test_data, safe_name, sha, source_payload
import platform_tools

ROOT = Path(__file__).resolve().parent


def package(game, archive, staging, output):
    native = platform_tools.host_platform()
    if native not in ('macos-arm64', 'linux-x64') or sys.version_info[:2] != (3, 11):
        raise ValueError('Package with native macOS ARM64 or Linux x64 Python 3.11')
    if staging.exists() or output.exists():
        raise ValueError('Use new staging and archive paths')
    if not output.name.endswith('.tar.gz'):
        raise ValueError('The POSIX launcher requires a .tar.gz archive')
    selected = {**games.load(ROOT, game), 'platform': native,
                'modName': game+'-cinematic-models-'+native,
                'installGuide': 'LAUNCHER-INSTALL-MAC.txt' if native == 'macos-arm64' else 'LAUNCHER-INSTALL-LINUX.txt'}
    config = games.compatibility(ROOT, selected)
    if sha(archive) != config['pythonRuntime']['sha256']:
        raise ValueError('Portable Python archive checksum failed')
    requirements = dict(line.split('==') for line in (ROOT/'requirements.txt').read_text().splitlines()
                        if line and not line.startswith('#'))
    if any(metadata.version(name) != version for name, version in requirements.items()):
        raise ValueError('Package with the pinned native dependencies')
    staging.mkdir(parents=True)
    with tarfile.open(archive) as portable:
        for member in portable:
            path = PurePosixPath(member.name)
            if native == 'linux-x64' and path.parts[:3] == ('python', 'share', 'terminfo'):
                # This noninteractive installer has no curses UI; terminal names collide by case.
                continue
            if path.suffix in ('.o', '.a', '.pyc') or '__pycache__' in path.parts or any(p.startswith('config-3.11-') for p in path.parts):
                continue
            portable.extract(member, staging/'.builder', filter='data')
    python = staging/'.builder/python/bin/python3.11'
    if not python.is_file():
        raise ValueError('Portable archive has no Python 3.11 interpreter')
    site = Path(sysconfig.get_path('purelib')).resolve()
    target = staging/'.builder/python/lib/python3.11/site-packages'
    for name in requirements:
        dist = metadata.distribution(name)
        for rel in dist.files or []:
            source = Path(dist.locate_file(rel)).resolve()
            if not source.is_file() or not source.is_relative_to(site) or '__pycache__' in source.parts:
                continue
            relative = source.relative_to(site)
            if dependency_test_data(relative.as_posix()):
                continue
            destination = target/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    for name, data in source_payload(selected).items():
        path = staging/'.builder/source'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    shutil.copy2(ROOT/'LauncherExtractor.sh', staging/'extractor')
    (staging/'extractor').chmod(0o755)
    for source, destination in ((selected['thumbnail'], 'thumbnail.png'),
                                ('CREDITS.txt', 'CREDITS.txt'),
                                (selected['installGuide'], 'INSTALL.txt')):
        shutil.copy2(ROOT/source, staging/destination)
    check = ('import json,sys,importlib.metadata as md; '
             'expected=json.loads(sys.argv[1]); '
             'assert all(md.version(k)==v for k,v in expected.items()); '
             'import numpy,scipy.spatial,PIL.Image,zstandard; '
             'assert numpy.allclose(numpy.linalg.solve([[2.,0.],[0.,4.]],[6.,8.]),[3.,2.]); '
             'assert scipy.spatial.cKDTree([[0.,0.],[2.,2.]]).query([.1,.1])[1]==0; '
             'assert PIL.Image.new("RGBA",(1,1),(1,2,3,255)).getpixel((0,0))==(1,2,3,255); '
             'sample=b"cinematic portable runtime"; '
             'assert zstandard.ZstdDecompressor().decompress(zstandard.ZstdCompressor().compress(sample))==sample')
    environment = {key: value for key, value in os.environ.items()
                   if key not in {'LD_LIBRARY_PATH', 'LD_PRELOAD'} and not key.startswith('DYLD_')}
    subprocess.run([str(python.resolve()), '-I', '-B', '-c', check, json.dumps(requirements)],
                   check=True, env=environment)
    subprocess.run([str((staging/'extractor').resolve()), '--help'], check=True,
                   stdout=subprocess.DEVNULL, env=environment)
    records = {}
    for path in sorted(staging.rglob('*')):
        if path.is_symlink() and not path.resolve().is_relative_to(staging.resolve()):
            raise ValueError('Portable runtime link escapes the package')
        if path.is_file():
            records[path.relative_to(staging).as_posix()] = sha(path)
    manifest = dict(schemaVersion=1, game=game, version=config['version'],
                    openGoalVersion=config['openGoalVersion'], platform=native,
                    gameAssetsIncluded=False, dependencies=requirements,
                    pythonRuntime=config['pythonRuntime'], files=records)
    (staging/'bundle-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, 'x:gz', compresslevel=6, dereference=True) as bundle:
        for name in sorted([*records, 'bundle-manifest.json']):
            safe_name(name)
            path = staging/name
            info = bundle.gettarinfo(path, arcname=name)
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ''
            with path.open('rb') as source:
                bundle.addfile(info, source)
    with tarfile.open(output, 'r:gz') as bundle:
        names = bundle.getnames()
        if len(names) != len(set(n.casefold() for n in names)) or set(names) != set(records)|{'bundle-manifest.json'}:
            raise ValueError('Unexpected or duplicate launcher archive entries')
        for member in bundle.getmembers():
            if not member.isfile():
                raise ValueError('Unexpected non-file launcher archive entry')
            if member.name in records and hashlib.sha256(bundle.extractfile(member).read()).hexdigest() != records[member.name]:
                raise ValueError('Launcher archive checksum failed: '+member.name)
        if json.load(bundle.extractfile('bundle-manifest.json')) != manifest:
            raise ValueError('Launcher archive manifest mismatch')
        for name in ('extractor', '.builder/python/bin/python3.11'):
            if not bundle.getmember(name).mode & 0o111:
                raise ValueError('Archive lost executable permissions')
    result = dict(game=game, platform=native, files=len(records),
                  bytes=output.stat().st_size, sha256=sha(output), gameAssetsIncluded=False)
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', choices=games.GAMES, required=True)
    for name in ('python-archive', 'staging', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    package(args.game, args.python_archive.resolve(), args.staging.resolve(), args.output.resolve())
