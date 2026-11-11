"""Assemble one game using the OG-Mod-Base release names and metadata."""
from __future__ import annotations
import argparse
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import prepare_release as shared

ROOT=Path(__file__).resolve().parents[1]


def verify_metadata(metadata,game):
    schema=json.loads((ROOT/'.github/schemas/mods/v2/mod-schema.v2.json').read_text())['definitions']['ModMetadata']
    if set(metadata)!=set(schema['required']):raise ValueError('Incorrect template metadata fields')
    for name in ('schemaVersion','version'):
        if not re.fullmatch(schema['properties'][name]['pattern'],metadata[name]):
            raise ValueError('Invalid metadata '+name)
    if metadata['supportedGames']!=[game]:raise ValueError('Metadata selects the wrong game')
    datetime.fromisoformat(metadata['publishedDate'])


def prepare(inputs,output,version=None):
    config=json.loads((ROOT/'mod-release.json').read_text())
    game=config['game'];version=version or config['version']
    if version!=config['version']:raise ValueError('Commit the new mod version before packaging it')
    inputs=Path(inputs).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Use a new release output directory')
    if inputs==output or output.is_relative_to(inputs):raise ValueError('Keep input and output separate')
    records=[];verified=[]
    for platform,prefix,extension in (('windows-x64','windows','zip'),('macos-arm64','macos-arm64','tar.gz')):
        ordinary=inputs/f'{game}-cinematic-models-{platform}.{extension}'
        standard=inputs/f'{prefix}-v{version}.{extension}'
        source=ordinary if ordinary.is_file() else standard
        if not source.is_file():raise ValueError('Missing installer: '+str(source))
        record=shared.verify_bundle(source,game,version,platform)
        name=f'{prefix}-v{version}.{extension}'
        record['filename']=name
        verified.append((source,name,record));records.append(record)
    output.mkdir(parents=True)
    for source,name,record in verified:
        shutil.copyfile(source,output/name)
        if shared.file_sha(output/name)!=record['sha256']:raise ValueError('Installer copy changed')
    source_name=f'source-v{version}.zip'
    module=runpy.run_path(str(ROOT/'builder/package_source.py'))
    with redirect_stdout(io.StringIO()):module['package'](output/source_name)
    source_record=shared.verify_source(output/source_name)
    source_record['filename']=source_name;records.append(source_record)
    environment={**os.environ,'SCHEMA_VERSION':'0.1.0','VERSION':'v'+version,
                 'SUPPORTED_GAMES':game,'OUT_DIR':str(output)}
    subprocess.run([sys.executable,str(ROOT/'.github/scripts/create-mod-release/emit-metadata.py')],
                   env=environment,check=True,stdout=subprocess.DEVNULL)
    metadata=json.loads((output/'metadata.json').read_text());verify_metadata(metadata,game)
    records.append(dict(filename='metadata.json',sha256=shared.file_sha(output/'metadata.json'),
                        bytes=(output/'metadata.json').stat().st_size))
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    manifest=dict(schemaVersion=1,game=game,version=version,repository=config['repository'],
                  sourceCommit=commit,openGoalVersion='v0.3.9',gameAssetsIncluded=False,files=records)
    (output/'release-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    names=[record['filename'] for record in records]+['release-manifest.json']
    (output/'SHA256SUMS.txt').write_text(''.join(shared.file_sha(output/name)+'  '+name+'\n' for name in names))
    base='https://github.com/'+config['repository']
    notes=(f"{config['displayName']} {version}\n\n{config['description']}\n\n"
           'Windows x64 and Apple Silicon macOS. Requires OpenGOAL v0.3.9 and a supported EU or USA game installation. '
           +('Jak 1 also requires vanilla Jak II in the same launcher. ' if game=='jak1' else '')+
           '\n\nDownload the Windows ZIP or Mac TAR.GZ. In OpenGOAL Launcher, select the game and open Features > Mods > Add from File. '
           'The installer builds the models from your own installed games. No game models or textures are included. Mod saves are separate from vanilla saves.\n\n'
           f'Comparisons: {base}/blob/main/docs/comparisons/{game}.md\n'
           f'Compatibility: {base}/blob/main/docs/verification.md\n'
           f'Source commit: {commit}\n\nDeveloped by @jacopobassan\nhttps://jacopobassan.me\n')
    (output/'RELEASE-NOTES.md').write_text(notes)
    # Check the one-download-per-platform contract used by JakMods.
    selected={'windows':None,'macos':None};meta=[]
    for name in names:
        if name.startswith('windows-'):
            if selected['windows']:raise ValueError('Ambiguous Windows downloads')
            selected['windows']=name
        elif name.startswith('macos-'):
            if selected['macos']:raise ValueError('Ambiguous Mac downloads')
            selected['macos']=name
        elif name=='metadata.json':meta.append(name)
    if None in selected.values() or len(meta)!=1:raise ValueError('Incomplete JakMods release')
    result=dict(passed=True,game=game,version=version,sourceCommit=commit,
                files=names+['SHA256SUMS.txt'],jakmodsAssets=selected,supportedGames=metadata['supportedGames'],
                sourceFiles=len(shared.source_files()),published=False)
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--version')
    arguments=parser.parse_args();prepare(arguments.input,arguments.output,arguments.version)
