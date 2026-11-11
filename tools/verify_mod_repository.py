"""Verify a game-only source tree, package inventory, and public resources."""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def verify():
    config = json.loads((ROOT/'mod-release.json').read_text())
    game = config['game']
    assert game in ('jak1', 'jak2', 'jak3')
    assert config['repository'] == 'JacopoBassan/the-cinematic-legacy-'+game
    builder = ROOT/'builder'
    inventory = json.loads((builder/'source-files.json').read_text())
    assert inventory['game'] == game and inventory['schemaVersion'] == 1
    source_names = inventory['sourceFiles']
    runtime_names = inventory['runtimeFiles']
    assert len(source_names) == len(set(source_names))
    assert len(runtime_names) == len(set(runtime_names))
    assert set(runtime_names) < set(source_names)
    actual = {p.relative_to(builder).as_posix() for p in builder.rglob('*') if p.is_file()
              and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    assert actual == set(source_names), sorted(actual.symmetric_difference(source_names))
    forbidden_runtime = {'package_source.py', 'package_launcher.py', 'package_macos.py',
                         'LauncherExtractor.cs', 'LauncherExtractor.sh', 'README.md',
                         'LAUNCHER-INSTALL.txt', 'LAUNCHER-INSTALL-MAC.txt'}
    assert not forbidden_runtime.intersection(runtime_names)
    for other in {'jak1', 'jak2', 'jak3'}-{game}:
        assert not (builder/other).exists(), other
        assert not (builder/f'game-configs/{other}.json').exists()
        assert not (builder/f'artwork/{other}.png').exists()
    assert {p.name for p in (builder/'artwork').iterdir()} == {game+'.png'}
    selected = json.loads((builder/'game-config.json').read_text())
    canonical = json.loads((builder/f'game-configs/{game}.json').read_text())
    assert selected == canonical and selected['game'] == game
    assert (ROOT/'artwork/thumbnail.png').read_bytes() == (builder/selected['thumbnail']).read_bytes()
    mac = json.loads((builder/'platforms/macos-arm64.json').read_text())
    assert set(mac['gameProfiles']) == {game}
    compatibility = json.loads((builder/selected['compatibilityFile']).read_text())
    assert compatibility['version'] == config['version']
    provenance = json.loads((ROOT/'mod-source-provenance.json').read_text())
    assert provenance['templateRepository'] == 'OpenGOAL-Mods/OG-Mod-Base'
    assert provenance['game'] == game and provenance['sourceLayout'] == 'per-game'
    assert set(provenance['builderFiles']) == actual
    for name, digest in provenance['builderFiles'].items():
        assert hashlib.sha256((builder/name).read_bytes()).hexdigest() == digest, name
    if game == 'jak1':
        assert provenance['windowsMouthFix']['commit'] == 'd34e41844372070fba6ddd647c27446580f7a463'
        assert hashlib.sha256((builder/'jak1/native_face_rig.py').read_bytes()).hexdigest() == provenance['windowsMouthFix']['fileSha256']
    for directory in ('goal_src', 'game', 'decompiler', 'third-party', 'test', 'out', 'common', 'goalc', 'lsp'):
        assert not (ROOT/directory).exists(), directory
    assert 'OpenGOAL Team' in (ROOT/'LICENSE').read_text()
    assert '@jacopobassan' in (ROOT/'MOD-LICENSE').read_text()
    manifest = json.loads((ROOT/'docs/comparisons/manifest.json').read_text())
    assert set(manifest['games']) == {game}
    for row in manifest['games'][game]['pairs']:
        path = ROOT/'docs/comparisons'/row['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
        with Image.open(path) as image:
            assert list(image.size) == row['dimensions']
    readme = (ROOT/'README.md').read_text()
    for sibling in ('jak1', 'jak2', 'jak3'):
        assert 'https://github.com/JacopoBassan/the-cinematic-legacy-'+sibling in readme
    for page in [ROOT/'README.md', *(ROOT/'docs').rglob('*.md')]:
        for reference in re.findall(r'!?\[[^\]]*\]\(([^)\s]+)\)', page.read_text()):
            url = urlsplit(reference)
            if url.scheme or url.netloc or not url.path:
                continue
            target = (page.parent/unquote(url.path)).resolve()
            assert target.is_relative_to(ROOT) and target.is_file(), (page.name, reference)
    workflow = (ROOT/'.github/workflows/mod-release-pipeline.yml').read_text()
    assert '--draft' in workflow and '--draft=false' not in workflow
    assert config['openGoalVersion'] == 'v0.3.9'
    for path in builder.rglob('*.py'):
        ast.parse(path.read_bytes(), filename=str(path))
    result = {'passed': True, 'game': game, 'sourceFiles': len(source_names),
              'runtimeFiles': len(runtime_names), 'foreignGameBuilders': 0,
              'comparisons': len(manifest['games'][game]['pairs']), 'releaseAlwaysDraft': True}
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    verify()
