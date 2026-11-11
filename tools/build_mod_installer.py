"""Build the selected game's installer with the existing pinned portable runtime."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]


def build(platform,output):
    config=json.loads((ROOT/'mod-release.json').read_text())
    pins=json.loads((ROOT/f'builder/platforms/{platform}.json').read_text())['pythonRuntime']
    if platform=='windows-x64' and os.name!='nt':raise ValueError('Build Windows on Windows')
    if platform=='macos-arm64' and sys.platform!='darwin':raise ValueError('Build Mac on Apple Silicon')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    game=config['game']
    with tempfile.TemporaryDirectory(prefix='cinematic-package-') as temporary:
        work=Path(temporary).resolve();archive=work/'python.tar.gz'
        digest=hashlib.sha256()
        with urlopen(pins['url'],timeout=120) as response,archive.open('wb') as stream:
            while block:=response.read(1024*1024):stream.write(block);digest.update(block)
        if digest.hexdigest()!=pins['sha256']:raise ValueError('Python runtime checksum mismatch')
        host=work/'host';host.mkdir()
        with tarfile.open(archive) as source:source.extractall(host,filter='data')
        python=host/('python/python.exe' if platform=='windows-x64' else 'python/bin/python3.11')
        subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','-r',str(ROOT/'builder/requirements.txt')],check=True)
        extension='zip' if platform=='windows-x64' else 'tar.gz'
        destination=output/f'{game}-cinematic-models-{platform}.{extension}'
        if platform=='windows-x64':
            compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
            command=[str(python),str(ROOT/'builder/package_launcher.py'),'--game',game,
                     '--compiler',str(compiler),'--python-archive',str(archive)]
        else:
            command=[str(python),str(ROOT/'builder/package_macos.py'),'--game',game,'--python-archive',str(archive)]
        command+=['--staging',str(work/'package'),'--output',str(destination)]
        subprocess.run(command,check=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform',choices=('windows-x64','macos-arm64'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();build(args.platform,args.output)
