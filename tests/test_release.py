"""Failure modes: mixed developer and runtime source manifests, missing or stale sources,
wrong-game bundles, corrupt hashes, ambiguous paths, unsafe TAR members or modes, and
changed runtime pins. These retained archive fixtures test rejected inputs without live
game data."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

import prepare_release as release
import game_config as games

GAME = games.GAME
OTHER_GAME = 'jak2' if GAME == 'jak1' else 'jak1'
SOURCE_MODULE = games.default_config()['builderModule'].replace('.', '/')+'.py'


class PublicBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def bundle(self, game=GAME, platform='windows-x64', *, extra=None, change=None,
               omit=None, corrupt=False, manifest_changes=None, members=None):
        sources, selected = release.selected_sources(game, platform)
        developer = release.source_files()
        files = {'.builder/source/' + name: data for name, data in sources.items()}
        for source, target in ((selected['thumbnail'], 'thumbnail.png'), ('CREDITS.txt', 'CREDITS.txt'),
                               (selected['installGuide'], 'INSTALL.txt')):
            files[target] = developer[source]
        if platform in ('macos-arm64', 'linux-x64'):
            files.update({'extractor': developer['LauncherExtractor.sh'],
                          '.builder/python/bin/python3.11': b'test native runtime'})
        else:
            files.update({'extractor.exe': b'test adapter', '.builder/python/python.exe': b'test runtime'})
        files.update(extra or {})
        files.update(change or {})
        for name in omit or ():
            del files[name]
        records = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        if corrupt:
            records['CREDITS.txt'] = '0' * 64
        dependencies = dict(line.split('==') for line in sources['requirements.txt'].decode().splitlines()
                            if line.strip() and not line.startswith('#'))
        manifest = {'schemaVersion': 1, 'game': game, 'platform': platform,
                    'openGoalVersion': json.loads(sources[selected['compatibilityFile']])['openGoalVersion'],
                    'dependencies': dependencies, 'gameAssetsIncluded': False, 'version': '1.0.0', 'files': records}
        if platform in ('macos-arm64', 'linux-x64'):
            manifest['pythonRuntime'] = json.loads(sources['platforms/'+platform+'.json'])['pythonRuntime']
        else:
            manifest['pythonRuntime'] = json.loads(developer['platforms/windows-x64.json'])['pythonRuntime']
        if platform == 'windows-x64':
            manifest['adapterSourceSha256'] = release.sha(developer['LauncherExtractor.cs'])
        manifest.update(manifest_changes or {})
        files['bundle-manifest.json'] = json.dumps(manifest).encode()
        extension = 'tar.gz' if platform in ('macos-arm64', 'linux-x64') else 'zip'
        path = self.root / f'{game}-cinematic-models-{platform}.{extension}'
        if platform in ('macos-arm64', 'linux-x64'):
            with tarfile.open(path, 'w:gz') as archive:
                for name, data in files.items():
                    item = tarfile.TarInfo(name)
                    item.size = len(data)
                    item.mode = 0o755 if name in ('extractor', '.builder/python/bin/python3.11') else 0o644
                    if members:
                        members(item)
                    archive.addfile(item, io.BytesIO(data) if item.isfile() else None)
        else:
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
        return path

    def test_verified_platforms_and_source_closure(self):
        for platform in release.PLATFORMS:
            with self.subTest(platform=platform):
                result = release.verify_bundle(self.bundle(platform=platform), GAME, '1.0.0', platform)
                self.assertEqual(result['game'], GAME)
                self.assertEqual(result['platform'], platform)
                self.assertEqual(result['sourceFiles'], len(release.runtime_files()))
                self.assertGreater(result['bytes'], 0)
                self.assertLess(set(release.runtime_files()), set(release.source_files()))

    def test_wrong_game_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Wrong game'):
            release.verify_bundle(self.bundle(manifest_changes={'game':OTHER_GAME}), GAME, '1.0.0')

    def test_corruption_rejected_for_each_platform(self):
        for platform in release.PLATFORMS:
            with self.subTest(platform=platform), self.assertRaisesRegex(ValueError, 'Checksum'):
                release.verify_bundle(self.bundle(platform=platform, corrupt=True), GAME, '1.0.0', platform)

    def test_game_assets_runtime_and_ambiguous_paths_rejected(self):
        names = (f'data/out/{GAME}/fr3/GAME.fr3', '.builder/source/donor.glb', '.builder/python/donor.gltf',
                 '.builder/source/native.go', 'gk.exe', '.builder/python/gk', '../escape.txt',
                 '.builder/source/../escape.txt', '.builder/source//escape.txt',
                 '.builder/source/./escape.txt', '.builder/source/C:escape.txt',
                 '.builder/source/escape.txt ', '.builder/source\\escape.txt')
        for platform in release.PLATFORMS:
            for name in names:
                with self.subTest(platform=platform, name=name), self.assertRaises(ValueError):
                    release.verify_bundle(self.bundle(platform=platform, extra={name: b'not distributable'}),
                                          GAME, '1.0.0', platform)

    def test_stale_missing_and_reselected_sources_rejected(self):
        for options in ({'omit': ['.builder/source/'+SOURCE_MODULE]},
                        {'change': {'.builder/source/'+SOURCE_MODULE: b'# stale source'}},
                        {'change': {'.builder/source/game-config.json': json.dumps({'game':OTHER_GAME}).encode()}},
                        {'extra': {'.builder/source/unreviewed.py': b'# extra source'}}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'Source closure'):
                release.verify_bundle(self.bundle(**options), GAME, '1.0.0')

    def test_mac_runtime_and_dependency_pins_checked(self):
        for changes in ({'pythonRuntime': {'sha256': '0' * 64}}, {'dependencies': {}},
                        {'platform': 'windows-x64'}, {'openGoalVersion': 'v0.0.0'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                release.verify_bundle(self.bundle(platform='macos-arm64', manifest_changes=changes),
                                      GAME, '1.0.0', 'macos-arm64')
        # The compiled Windows adapter retains its source pin without shipping C#.
        for adapter_hash in (None, '0' * 64):
            with self.subTest(adapter_hash=adapter_hash), self.assertRaises(ValueError):
                release.verify_bundle(self.bundle(manifest_changes={'adapterSourceSha256':adapter_hash}),
                                      GAME, '1.0.0', 'windows-x64')

    def test_mac_launcher_and_interpreter_require_executable_mode(self):
        for name in ('extractor', '.builder/python/bin/python3.11'):
            for mode in (0o644, 0o645, 0o654):
                def remove_owner_executable(item):
                    if item.name == name:
                        item.mode = mode
                with self.subTest(name=name, mode=oct(mode)), self.assertRaisesRegex(ValueError, 'executable'):
                    release.verify_bundle(self.bundle(platform='macos-arm64', members=remove_owner_executable),
                                          GAME, '1.0.0', 'macos-arm64')

    def test_mac_links_devices_and_privileged_modes_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.DIRTYPE, 'setuid'):
            def invalid_member(item):
                if item.name == 'extractor':
                    if kind == 'setuid':
                        item.mode = 0o4755
                    else:
                        item.type, item.linkname = kind, '../outside'
                        item.size = 0
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'non-file or privileged'):
                release.verify_bundle(self.bundle(platform='macos-arm64', members=invalid_member),
                                      GAME, '1.0.0', 'macos-arm64')

    def test_case_collisions_rejected_for_each_platform(self):
        for platform in release.PLATFORMS:
            with self.subTest(platform=platform), self.assertRaisesRegex(ValueError, 'Duplicate'):
                release.verify_bundle(self.bundle(platform=platform, extra={'.builder/source/'+SOURCE_MODULE.upper(): b'duplicate'}),
                                      GAME, '1.0.0', platform)

    def test_source_zip_matches_the_current_reviewed_closure(self):
        path = self.root / 'source.zip'
        for stale in (False, True):
            files = release.source_files()
            if stale:
                files[SOURCE_MODULE] = b'# old source'
            records = {name: release.sha(data) for name, data in files.items()}
            with zipfile.ZipFile(path, 'w') as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
                archive.writestr('source-manifest.json', json.dumps({'schemaVersion': 1, 'game': GAME,
                                  'assetFreeSource': True, 'files': records}))
            if stale:
                with self.assertRaisesRegex(ValueError, 'reviewed checkout'):
                    release.verify_source(path)
            else:
                self.assertEqual(release.verify_source(path)['sourceFiles'], len(files))


if __name__=='__main__':unittest.main()
