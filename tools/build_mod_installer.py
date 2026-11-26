"""Build the selected game's installer with the existing pinned portable runtime."""
from __future__ import annotations
import argparse
import hashlib
from http.client import IncompleteRead
import json
import os
import platform as native_platform
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]


def download_runtime(url, expected_sha256, archive):
    for attempt in range(1, 4):
        digest = hashlib.sha256()
        try:
            with urlopen(url, timeout=120) as response, archive.open('wb') as stream:
                size = response.headers.get('Content-Length')
                while block := response.read(1024 * 1024):
                    stream.write(block)
                    digest.update(block)
                if size is not None and stream.tell() != int(size):
                    raise ConnectionError(f'incomplete HTTP body ({stream.tell()} of {size} bytes)')
        except (ConnectionError, IncompleteRead, TimeoutError, URLError) as error:
            retryable = not isinstance(error, HTTPError) or error.code in (408, 429, 500, 502, 503, 504)
            if attempt == 3 or not retryable:
                raise
            print(f'Python runtime download attempt {attempt}/3 failed: {error}. Retrying.',
                  file=sys.stderr, flush=True)
            time.sleep(attempt)
        else:
            break

    if digest.hexdigest() != expected_sha256:
        raise ValueError('Python runtime checksum mismatch')


def build(platform,output):
    config=json.loads((ROOT/'mod-release.json').read_text())
    pins=json.loads((ROOT/f'builder/platforms/{platform}.json').read_text())['pythonRuntime']
    if platform=='windows-x64' and os.name!='nt':raise ValueError('Build Windows on Windows')
    if platform=='macos-arm64' and sys.platform!='darwin':raise ValueError('Build Mac on Apple Silicon')
    if platform=='linux-x64' and (sys.platform!='linux' or native_platform.machine().lower() not in ('x86_64','amd64')):
        raise ValueError('Build Linux on Linux x64')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    game=config['game']
    runtime_environment={key:value for key,value in os.environ.items()
                         if not key.startswith(('LD_','DYLD_')) and key not in ('PYTHONHOME','PYTHONPATH')}
    with tempfile.TemporaryDirectory(prefix='cinematic-package-') as temporary:
        work=Path(temporary).resolve();archive=work/'python.tar.gz'
        download_runtime(pins['url'], pins['sha256'], archive)
        host=work/'host';host.mkdir()
        with tarfile.open(archive) as source:source.extractall(host,filter='data')
        python=host/('python/python.exe' if platform=='windows-x64' else 'python/bin/python3.11')
        subprocess.run([str(python),'-m','pip','install','--only-binary=:all:','--disable-pip-version-check','-r',str(ROOT/'builder/requirements.txt')],check=True,env=runtime_environment)
        extension='zip' if platform=='windows-x64' else 'tar.gz'
        destination=output/f'{game}-cinematic-models-{platform}.{extension}'
        if platform=='windows-x64':
            compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
            command=[str(python),str(ROOT/'builder/package_launcher.py'),'--game',game,
                     '--compiler',str(compiler),'--python-archive',str(archive)]
        else:
            command=[str(python),str(ROOT/'builder/package_posix.py'),'--game',game,'--python-archive',str(archive)]
        command+=['--staging',str(work/'package'),'--output',str(destination)]
        subprocess.run(command,check=True,env=runtime_environment)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform',choices=('windows-x64','macos-arm64','linux-x64'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();build(args.platform,args.output)
