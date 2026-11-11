"""Build one asset-free Windows launcher bundle for a selected game."""
from __future__ import annotations
import argparse
import ast
import hashlib
import io
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import zipfile
import game_config as games
from package_source import FILES, RUNTIME_FILES, ROOT

FORBIDDEN = {'.fr3','.cgo','.dgo','.glb','.gltf','.go','.iso','.o','.gc','.asm','.sav','.blend'}
PYTHON_RUNTIME = json.loads((ROOT/'platforms/windows-x64.json').read_text())['pythonRuntime']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or path.suffix.lower() in FORBIDDEN:
        raise ValueError('Unsafe archive path or game asset: ' + name)


def dependency_test_data(relative):
    """Strip test directories, retaining import helpers and distribution notices."""
    directories = PurePosixPath(relative).parts[:-1]
    if any(part.lower().endswith(('.dist-info', '.egg-info')) for part in directories):
        return False
    return any(part.lower() in {'test', 'tests'} for part in directories)


def source_payload(selected):
    sources = {}
    if len(RUNTIME_FILES) != len(set(RUNTIME_FILES)):
        raise ValueError('Duplicate public source entry')
    for name in RUNTIME_FILES:
        safe_name(name)
        path = games.relative_file(ROOT, name)
        if not path.is_file() or path.is_symlink():
            raise ValueError('Missing or linked public source: ' + name)
        data = path.read_bytes()
        if path.suffix == '.py':ast.parse(data, filename=name)
        if path.suffix != '.png' and re.search(r'(?i)[a-z]:[\\/]Users[\\/]', data.decode('utf-8')):
            raise ValueError('Private path in public source: ' + name)
        sources[name] = data
    # Each installer carries only its selected game runtime sources.
    sources['game-config.json'] = (json.dumps(selected, indent=2)+'\n').encode()
    return sources


def reuse_runtime(staging, original, requirements):
    manifest = json.loads((original/'bundle-manifest.json').read_text())
    if manifest.get('pythonRuntime') != PYTHON_RUNTIME:
        raise ValueError('The reusable bundle has a different pinned Python runtime')
    if manifest.get('gameAssetsIncluded') is not False or manifest.get('dependencies') != requirements:
        raise ValueError('The reusable bundle has different pinned dependencies')
    if manifest.get('adapterSourceSha256') != sha(ROOT/'LauncherExtractor.cs'):
        raise ValueError('Compile the changed launcher adapter instead of reusing it')
    tools = {n:h for n,h in manifest['files'].items()
             if n == 'extractor.exe' or
             (n.startswith('.builder/python/') and not dependency_test_data(n))}
    if '.builder/python/python.exe' not in tools or 'extractor.exe' not in tools:
        raise ValueError('Reusable bundle has no portable Python or launcher adapter')
    for name, digest in tools.items():
        safe_name(name)
        source = games.relative_file(original, name)
        if source.is_symlink() or not source.is_file() or sha(source) != digest:
            raise ValueError('Reusable runtime checksum failed: ' + name)
        destination = staging/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha(destination) != digest:
            raise ValueError('Reusable runtime copy changed: ' + name)
    return tools


def verify_python_archive(archive):
    if archive is None or not Path(archive).is_file():
        raise ValueError('Pinned Windows Python archive checksum failed')
    data = Path(archive).read_bytes()
    if hashlib.sha256(data).hexdigest() != PYTHON_RUNTIME['sha256']:
        raise ValueError('Pinned Windows Python archive checksum failed')
    return data


def extract_runtime(archive, py):
    """Copy only the portable runtime's canonical files from the pinned archive."""
    core = {'python.exe', 'python3.dll', 'python311.dll', 'vcruntime140.dll',
            'vcruntime140_1.dll', 'LICENSE.txt'}
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as source, zipfile.ZipFile(py/'python311.zip', 'w', zipfile.ZIP_DEFLATED) as stdlib:
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name or ':' in member.name:
                raise ValueError('Unsafe Python archive path: '+member.name)
            key = member.name.casefold()
            if key in seen:
                raise ValueError('Duplicate Python archive path: '+member.name)
            seen.add(key)
            parts = path.parts
            if not member.isfile() or len(parts) < 2 or parts[0] != 'python':
                continue
            relative = parts[1:]
            data = None
            if len(relative) == 1 and relative[0] in core:
                data = source.extractfile(member).read()
                (py/relative[0]).write_bytes(data)
            elif relative[0] == 'DLLs' and len(relative) > 1 and not relative[-1].lower().endswith(('.pdb','.lib','.exp')) and not relative[-1].lower().startswith(('tcl','tk')) and relative[-1].lower() != '_tkinter.pyd':
                dest = py.joinpath(*relative)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as incoming, dest.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
            elif relative[0] == 'Lib' and len(relative) > 1 and relative[-1].endswith('.py') and relative[1] not in ('site-packages','test','tests','idlelib','tkinter','ensurepip') and '__pycache__' not in relative:
                stdlib.writestr('/'.join(relative[1:]), source.extractfile(member).read())
    if not all((py/name).is_file() for name in core) or not (py/'DLLs').is_dir():
        raise ValueError('Pinned Python archive is missing required runtime files')


def bundle_runtime(staging, compiler, requirements, python_archive):
    if compiler is None:
        raise ValueError('Supply --compiler or --runtime-from')
    if '.'.join(map(str,sys.version_info[:3])) != PYTHON_RUNTIME['version']:
        raise ValueError('Package with pinned Python '+PYTHON_RUNTIME['version'])
    py = staging/'.builder/python'
    py.mkdir(parents=True)
    extract_runtime(python_archive, py)
    site = Path(sysconfig.get_path('purelib'))
    target = py/'Lib/site-packages'
    target.mkdir(parents=True)
    for name in requirements:
        dist = metadata.distribution(name)
        for rel in dist.files or []:
            path = Path(dist.locate_file(rel)).resolve()
            if not path.is_relative_to(site.resolve()) or not path.is_file() or '__pycache__' in path.parts:
                continue
            relative = path.relative_to(site.resolve())
            if dependency_test_data(relative.as_posix()):
                continue
            dest = target/relative
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,dest)
    (py/'python311._pth').write_text('python311.zip\nDLLs\nLib/site-packages\n../source\n../source/stages\nimport site\n')
    subprocess.run([str(compiler),'/nologo','/target:exe','/platform:x64','/optimize+',
                    '/out:'+str(staging/'extractor.exe'),str(ROOT/'LauncherExtractor.cs')],check=True)
    return {}


def verify_archive(output, expected, game):
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        if len(names) != len(set(n.casefold() for n in names)) or set(names) != set(expected)|{'bundle-manifest.json'}:
            raise ValueError('Unexpected or duplicate launcher archive entries')
        if archive.testzip() is not None:
            raise ValueError('Invalid launcher archive CRC')
        manifest = json.loads(archive.read('bundle-manifest.json'))
        if manifest['files'] != expected or manifest['game'] != game:
            raise ValueError('Launcher archive manifest mismatch')
        for name, digest in expected.items():
            safe_name(name)
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError('Launcher archive checksum failed: ' + name)


def package(output, staging, compiler=None, *, game=games.GAME, runtime_from=None, python_archive=None):
    output, staging = Path(output), Path(staging)
    if output.exists() or staging.exists():
        raise ValueError('Use new archive and staging paths.')
    if sys.version_info[:2] != (3,11) or sys.platform != 'win32':
        raise ValueError('Package with the tested Windows x64 Python 3.11 environment.')
    selected = games.load(ROOT, game)
    config = games.compatibility(ROOT, selected)
    if config['platform'] != 'windows-x64':
        raise ValueError('Only the validated Windows x64 platform can be packaged')
    archive_bytes = None
    if runtime_from is None:
        archive_bytes = verify_python_archive(python_archive)
    elif python_archive is not None:
        raise ValueError('Use --python-archive only with --compiler')
    requirements = dict(line.split('==') for line in (ROOT/'requirements.txt').read_text().splitlines()
                        if line.strip() and not line.startswith('#'))
    if runtime_from is None:
        for name,version in requirements.items():
            if metadata.version(name) != version:
                raise ValueError(f'Packaging requires {name}=={version}')
    sources = source_payload(selected)
    staging.mkdir(parents=True)
    for name,data in sources.items():
        target = staging/'.builder/source'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
    preserved = (reuse_runtime(staging,Path(runtime_from),requirements) if runtime_from is not None
                 else bundle_runtime(staging,compiler,requirements,archive_bytes))
    for source,target in ((selected['thumbnail'],'thumbnail.png'),('CREDITS.txt','CREDITS.txt'),
                          (selected['installGuide'],'INSTALL.txt')):
        shutil.copy2(games.relative_file(ROOT,source),staging/target)
    subprocess.run([str((staging/'.builder/python/python.exe').resolve()),'-I','-B','-c',
        'import os; os.environ.setdefault("CINEMATIC_BUILD_WORK",os.getcwd()); '
        'import numpy,scipy.spatial,PIL.Image,zstandard,game_config,launcher_install; '
        'assert numpy.allclose(numpy.linalg.solve([[2.,0.],[0.,4.]],[6.,8.]),[3.,2.]); '
        'distance,index=scipy.spatial.cKDTree([[0.,0.],[2.,2.]]).query([.1,.1]); '
        'assert index==0 and distance<.2; '
        'picture=PIL.Image.new("RGBA",(1,1),(1,2,3,255)); assert picture.getpixel((0,0))==(1,2,3,255); '
        'sample=b"cinematic portable runtime"; '
        'assert zstandard.ZstdDecompressor().decompress(zstandard.ZstdCompressor().compress(sample))==sample; '
        'import importlib.metadata as md,json,sys; '
        'assert sys.version_info[:3]==tuple(map(int,sys.argv[3].split("."))); '
        'expected=json.loads(sys.argv[2]); assert all(md.version(k)==v for k,v in expected.items()); '
        'config=game_config.load(); assert config["game"]==sys.argv[1]; '
        'module=game_config.builder(config); assert callable(module.preflight) and callable(module.build); '
        'print("Isolated NumPy/SciPy/Pillow/Zstandard operations and selected game builder pass")',game,json.dumps(requirements),PYTHON_RUNTIME['version']],check=True)
    records = {p.relative_to(staging).as_posix():sha(p) for p in sorted(staging.rglob('*'))
               if p.is_file() and '__pycache__' not in p.parts}
    for name in records:safe_name(name)
    if any(records.get(name) != digest for name,digest in preserved.items()):
        raise ValueError('Bundled tool bytes changed while packaging')
    manifest = dict(schemaVersion=1,game=game,version=config['version'],openGoalVersion=config['openGoalVersion'],
                    platform='windows-x64',gameAssetsIncluded=False,python=sys.version,
                    adapterSourceSha256=sha(ROOT/'LauncherExtractor.cs'),
                    pythonRuntime=PYTHON_RUNTIME,dependencies=requirements,files=records)
    (staging/'bundle-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name in sorted([*records,'bundle-manifest.json']):archive.write(staging/name,name)
    verify_archive(output,records,game)
    result = dict(archive=str(output),game=game,bytes=output.stat().st_size,
                  files=len(records),sourceFiles=len(sources),sha256=sha(output),
                  reusedToolFiles=len(preserved),gameAssetsIncluded=False)
    print(json.dumps(result,indent=2))
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game',choices=games.GAMES,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--staging',type=Path,required=True)
    tools=parser.add_mutually_exclusive_group(required=True)
    tools.add_argument('--compiler',type=Path)
    tools.add_argument('--runtime-from',type=Path,help='Previously manifest-verified portable bundle staging folder')
    parser.add_argument('--python-archive',type=Path,help='Pinned Python runtime archive, required with --compiler')
    args=parser.parse_args()
    output=args.output or ROOT/'dist'/f'{args.game}-cinematic-models-windows-x64.zip'
    package(output.resolve(),args.staging.resolve(),args.compiler.resolve() if args.compiler else None,
            game=args.game,runtime_from=args.runtime_from.resolve() if args.runtime_from else None,
            python_archive=args.python_archive.resolve() if args.python_archive else None)
