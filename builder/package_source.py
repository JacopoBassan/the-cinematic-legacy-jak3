"""Create an asset-free source archive from an explicit, reviewed allowlist."""
import argparse,ast,hashlib,json,re,zipfile
from pathlib import Path,PurePosixPath

ROOT=Path(__file__).resolve().parent
LAYOUT=json.loads((ROOT/'source-files.json').read_text())
GAME=LAYOUT['game']
FILES=LAYOUT['sourceFiles']
RUNTIME_FILES=LAYOUT['runtimeFiles']
if (LAYOUT.get('schemaVersion')!=1 or not set(RUNTIME_FILES).issubset(FILES)
        or len(RUNTIME_FILES)!=len(set(n.casefold() for n in RUNTIME_FILES))):
    raise ValueError('Invalid game source inventory')

for name in FILES:
    path=PurePosixPath(name)
    if ((path.parts[0] in ('jak1','jak2','jak3') and path.parts[0]!=GAME)
            or (path.parts[0]=='game-configs' and name!=f'game-configs/{GAME}.json')
            or (path.parts[0]=='artwork' and name!=f'artwork/{GAME}.png')):
        raise ValueError('Source inventory includes another game: '+name)

def package(output):
    if output.exists():
        raise ValueError('Choose a new source archive filename')
    hashes={}
    sources={}
    if len(FILES)!=len(set(name.casefold() for name in FILES)):
        raise ValueError('Duplicate public source entry')
    for name in FILES:
        relative=PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
            raise ValueError(f'Unsafe source archive path: {name}')
        if relative.suffix.lower() in {'.fr3','.cgo','.dgo','.glb','.gltf','.go','.iso','.o','.gc','.asm'}:
            raise ValueError(f'Game asset in source allowlist: {name}')
        path=ROOT/name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Missing or linked public source file: {name}')
        data=path.read_bytes()
        if path.suffix=='.py':ast.parse(data,filename=name)
        if path.suffix.lower() not in ('.png',):
            text=data.decode('utf-8')
            if re.search(r'(?i)[a-z]:[\\/]Users[\\/]',text):
                raise ValueError(f'Private absolute path in public source: {name}')
        hashes[name]=hashlib.sha256(data).hexdigest()
        sources[name]=data
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name in sorted(FILES):
            info=zipfile.ZipInfo(name,date_time=(2026,10,23,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,sources[name])
        info=zipfile.ZipInfo('source-manifest.json',date_time=(2026,10,23,0,0,0))
        info.compress_type=zipfile.ZIP_DEFLATED
        archive.writestr(info,json.dumps({'schemaVersion':1,'assetFreeSource':True,'game':GAME,'files':hashes},indent=2))
    with zipfile.ZipFile(output) as archive:
        if set(archive.namelist())!=set(FILES)|{'source-manifest.json'} or archive.testzip() is not None:
            raise ValueError('Source archive verification failed')
        for name,digest in hashes.items():
            if hashlib.sha256(archive.read(name)).hexdigest()!=digest:
                raise ValueError(f'Source archive checksum mismatch: {name}')
    print(json.dumps({'archive':str(output),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'sourceFiles':len(FILES)},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    package(parser.parse_args().output.resolve())
