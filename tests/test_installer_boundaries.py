"""Failure modes: wrong-game configuration, source or runtime tampering, save changes,
unsafe archives, unverified tool versions and missing required local donors. These
retained fixtures use no game assets."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from unittest.mock import Mock
import zipfile

import game_config as games
import install as installer
import launcher_install as adapter
import package_launcher
import package_source

GAME = games.GAME
OTHER_GAME = 'jak2' if GAME == 'jak1' else 'jak1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class InstallerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cinematic-installer-boundary-')
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_windows_runtime_archive_rejected_before_staging(self):
        archive = self.root/'wrong.tar.gz'
        archive.write_bytes(b'wrong runtime')
        staging, output = self.root/'stage', self.root/'bundle.zip'
        with patch.object(package_launcher, 'sys', SimpleNamespace(version_info=(3, 11), platform='win32')), \
             patch.object(package_launcher.games, 'load', return_value={}), \
             patch.object(package_launcher.games, 'compatibility', return_value={'platform':'windows-x64'}), \
             patch.object(package_launcher, 'source_payload') as source_payload:
            with self.assertRaisesRegex(ValueError, 'archive checksum'):
                package_launcher.package(output, staging, self.root/'compiler.exe', python_archive=archive)
        source_payload.assert_not_called()
        self.assertFalse(staging.exists())
        self.assertFalse(output.exists())

    def test_windows_runtime_extracts_only_portable_files(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            for name in ('python.exe','python3.dll','python311.dll','vcruntime140.dll','vcruntime140_1.dll','LICENSE.txt',
                         'DLLs/_socket.pyd','DLLs/_tkinter.pyd','Lib/os.py','Lib/test/test_bad.py',
                         'Lib/site-packages/not_pinned.py','unwanted.txt'):
                data = name.encode(); entry = tarfile.TarInfo('python/'+name); entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        destination = self.root/'python'; destination.mkdir()
        package_launcher.extract_runtime(buffer.getvalue(), destination)
        self.assertEqual((destination/'DLLs/_socket.pyd').read_bytes(), b'DLLs/_socket.pyd')
        self.assertFalse((destination/'DLLs/_tkinter.pyd').exists())
        self.assertFalse((destination/'unwanted.txt').exists())
        with zipfile.ZipFile(destination/'python311.zip') as archive:
            self.assertEqual(archive.namelist(), ['os.py'])

    def fixture(self, game):
        root = self.root/game
        public = root/'source'
        public.mkdir(parents=True)
        selected = games.default_config(game)
        (public/'game-config.json').write_text(json.dumps(selected))
        descriptor = public/'game-configs'/f'{game}.json'
        descriptor.parent.mkdir()
        descriptor.write_text(json.dumps(selected))
        runtime = b'synthetic runtime'
        config = dict(game=game, version='fixture', openGoalVersion='v0.3.8', platform='windows-x64',
                      tools={'gk.exe':hashlib.sha256(runtime).hexdigest()}, payloadPaths=[])
        payload = root/'payload'
        entries = []
        for name in (f'data/out/{game}/fr3/GAME.fr3', f'data/out/{game}/iso/GAME.CGO'):
            path = payload/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(('generated '+game+name).encode())
            entries.append(dict(path=name,bytes=path.stat().st_size,sha256=sha(path)))
        config['payloadPaths'] = [e['path'] for e in entries]
        path = public/selected['compatibilityFile']
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(config))
        (public/'artwork').mkdir()
        (public/selected['thumbnail']).write_bytes(b'authored thumbnail fixture')
        (public/'CREDITS.txt').write_text('Fixture credits')
        manifest = dict(schemaVersion=1,game=game,version='fixture',openGoalVersion='v0.3.8',
                        platform='windows-x64',runtimeSha256=config['tools']['gk.exe'],files=entries)
        (payload/'manifest.json').write_text(json.dumps(manifest))
        mod = root/'OpenGOAL/features'/game/'mods/author/Models'
        mod.mkdir(parents=True)
        (mod/'gk.exe').write_bytes(runtime)
        (mod/'saved-progress.bin').write_bytes(b'untouched progress')
        return public,selected,config,payload,mod,root/'rollback'

    def test_each_game_update_validates_exact_payload_and_preserves_save(self):
        for game in games.GAMES:
            with self.subTest(game=game):
                public,selected,config,payload,mod,backup = self.fixture(game)
                with patch.object(installer,'ROOT',public), patch.object(installer,'require_game_closed'):
                    installer.install(payload,mod,backup,game_config=selected)
                self.assertEqual((mod/'saved-progress.bin').read_bytes(),b'untouched progress')
                self.assertEqual(json.loads((mod/'_metadata.json').read_text())['supportedGames'],[game])
                self.assertEqual((mod/f'data/out/{game}/fr3/GAME.fr3').read_bytes(),
                                 (payload/f'data/out/{game}/fr3/GAME.fr3').read_bytes())
                self.assertTrue((backup/'rollback.json').is_file())

    def test_wrong_game_path_and_payload_are_refused_before_backup(self):
        public,selected,config,payload,mod,backup = self.fixture(GAME)
        with patch.object(installer,'ROOT',public), patch.object(installer,'require_game_closed'):
            with self.assertRaisesRegex(ValueError,'Destination'):
                installer.install(payload,self.root/'OpenGOAL/features'/OTHER_GAME/'mods/a/b',backup,game_config=selected)
            manifest = json.loads((payload/'manifest.json').read_text())
            manifest['game']=OTHER_GAME
            (payload/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'different game'):
                installer.install(payload,mod,backup,game_config=selected)
        self.assertFalse(backup.exists())

    def test_direct_first_install_requires_launcher_for_every_game(self):
        for game in games.GAMES:
            with self.subTest(game=game):
                public,selected,config,payload,mod,backup=self.fixture(game)
                new_mod=mod.parent/'New Models'
                data=self.root/'original'/game/'data'
                tools=self.root/'original'/game/'bin'
                tools.mkdir(parents=True)
                for folder in ('iso_data', 'out'):
                    (data/folder).mkdir(parents=True)
                    (data/folder/'original.txt').write_text('unchanged original')
                (tools/'gk.exe').write_bytes(b'original runtime')
                before={p.relative_to(self.root).as_posix():sha(p)
                        for p in self.root.rglob('*') if p.is_file()}
                with patch.object(installer,'ROOT',public), \
                     patch.object(installer,'verify_payload') as verify, \
                     patch.object(installer,'require_game_closed') as closed, \
                     patch.object(installer.shutil,'copytree') as copy:
                    for options in ({}, {'base_data':data,'tools':tools}):
                        with self.assertRaisesRegex(ValueError,'import the matching launcher ZIP'):
                            installer.install(payload,new_mod,backup,game_config=selected,**options)
                    with patch('sys.argv',['install.py','--payload',str(payload),'--mod',str(new_mod),'--game',game]):
                        with self.assertRaisesRegex(ValueError,'import the matching launcher ZIP'):
                            installer.main()
                    verify.assert_not_called()
                    closed.assert_not_called()
                    copy.assert_not_called()
                self.assertFalse(new_mod.exists())
                self.assertFalse(backup.exists())
                after={p.relative_to(self.root).as_posix():sha(p)
                       for p in self.root.rglob('*') if p.is_file()}
                self.assertEqual(before,after)

    def test_launcher_bundle_source_tampering_is_refused(self):
        mod=self.root/'bundle';mod.mkdir()
        selected=games.default_config()
        module=selected['builderModule'].replace('.', '/')+'.py'
        names=['extractor.exe','.builder/source/launcher_install.py','.builder/source/game-config.json',
               '.builder/source/game_config.py','.builder/source/'+selected['compatibilityFile'],
               '.builder/source/'+module]
        hashes={}
        for name in names:
            path=mod/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture source')
            hashes[name]=sha(path)
        (mod/'bundle-manifest.json').write_text(json.dumps(dict(schemaVersion=1,game=GAME,gameAssetsIncluded=False,files=hashes)))
        adapter.verify_bundle(mod,selected)
        (mod/'.builder/source'/module).write_bytes(b'tampered source')
        with self.assertRaisesRegex(ValueError,'checksum'):
            adapter.verify_bundle(mod,selected)
    def test_descriptor_cannot_select_arbitrary_code_or_paths(self):
        selected = games.default_config()
        with self.assertRaises(ValueError):games.validate({**selected,'builderModule':'os'})
        with self.assertRaises(ValueError):games.validate({**selected,'compatibilityFile':'../secret'})
        with self.assertRaises(ValueError):games.validate({**selected,'runtimeFolders':['../../saves']})
        with self.assertRaises(ValueError):games.validate({**selected,'donorGame':'unsupported'})

    def test_pinned_version_tools_are_preferred_and_bad_active_tools_rejected(self):
        root = self.root/'OpenGOAL'
        pinned = root/'versions/official/v0.3.8'
        active = root/'active/bin'
        pinned.mkdir(parents=True);active.mkdir(parents=True)
        config = dict(openGoalVersion='v0.3.8',tools={})
        for name in ('gk.exe','goalc.exe','extractor.exe'):
            (pinned/name).write_bytes(('pinned '+name).encode())
            (active/name).write_bytes(b'newer unsupported version')
            config['tools'][name]=sha(pinned/name)
        self.assertEqual(adapter.select_tools(root,config),pinned)
        (pinned/'gk.exe').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'v0.3.8'):
            adapter.select_tools(root,config)

    def test_launcher_game_locations_do_not_cross_active_games(self):
        for game in games.GAMES:
            selected = games.default_config(game)
            mod = self.root/'OpenGOAL/features'/game/'mods/author/Models'
            data = self.root/'OpenGOAL/active'/game/'data'
            self.assertEqual(adapter.locations(mod,data/'iso_data'/game,selected)[0],data)
            other = 'jak1' if game != 'jak1' else 'jak2'
            with self.assertRaises(ValueError):
                adapter.locations(mod,self.root/'OpenGOAL/active'/other/'data/iso_data'/other,selected)



    def test_source_archive_refuses_assets_and_duplicate_paths(self):
        self.assertFalse(any(Path(name).name.startswith('test_') or
                             'tests' in Path(name).parts for name in package_source.FILES))
        (self.root/'local.py').write_text('VALUE = 3\n')
        with patch.object(package_source,'ROOT',self.root), patch.object(package_source,'FILES',['local.py']):
            output = self.root/'source.zip'
            package_source.package(output)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()),{'local.py','source-manifest.json'})
        with patch.object(package_source,'ROOT',self.root), patch.object(package_source,'FILES',['game.fr3']):
            with self.assertRaisesRegex(ValueError,'Game asset'):
                package_source.package(self.root/'bad.zip')
        with patch.object(package_source,'ROOT',self.root), patch.object(package_source,'FILES',['local.py','LOCAL.py']):
            with self.assertRaisesRegex(ValueError,'Duplicate'):
                package_source.package(self.root/'duplicate.zip')

    def test_package_selected_descriptor_and_corrupt_archive(self):
        stale={**games.default_config(), 'platform':'macos-arm64',
               'modName':GAME+'-cinematic-models-macos-arm64', 'installGuide':'LAUNCHER-INSTALL-MAC.txt'}
        (self.root/'game-config.json').write_text(json.dumps(stale))
        selected=games.default_config()
        with patch.object(package_launcher,'ROOT',self.root), patch.object(package_launcher,'RUNTIME_FILES',['game-config.json']):
            sources=package_launcher.source_payload(selected)
        self.assertEqual(json.loads(sources['game-config.json']),selected)
        output=self.root/'bundle.zip'
        data=b'actual bytes'
        expected={'extractor.exe':hashlib.sha256(b'different bytes').hexdigest()}
        with zipfile.ZipFile(output,'w') as archive:
            archive.writestr('extractor.exe',data)
            archive.writestr('bundle-manifest.json',json.dumps(dict(game=GAME,files=expected)))
        with self.assertRaisesRegex(ValueError,'checksum'):
            package_launcher.verify_archive(output,expected,GAME)
    def test_dependency_tests_are_stripped_but_helpers_and_notices_survive(self):
        original=self.root/'original-bundle'
        source=self.root/'source'
        source.mkdir()
        (source/'LauncherExtractor.cs').write_text('// synthetic adapter\n')
        keep=['extractor.exe','.builder/python/python.exe',
              '.builder/python/Lib/site-packages/scipy/_lib/_testutils.py',
              '.builder/python/Lib/site-packages/numpy/_pytesttester.py',
              '.builder/python/Lib/site-packages/numpy/testing/_private/utils.py',
              '.builder/python/Lib/site-packages/scipy.dist-info/METADATA',
              '.builder/python/Lib/site-packages/scipy.dist-info/licenses/LICENSE.txt',
              '.builder/python/Lib/site-packages/scipy.dist-info/licenses/tests/NOTICE.txt']
        remove=['.builder/python/Lib/site-packages/scipy/io/tests/data/array_float32_1d.sav',
                '.builder/python/Lib/site-packages/PIL/test/fixture.blend']
        records={}
        for name in keep+remove:
            path=original/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(('fixture '+name).encode())
            records[name]=sha(path)
        requirements={'scipy':'fixture'}
        (original/'bundle-manifest.json').write_text(json.dumps(dict(
            gameAssetsIncluded=False,dependencies=requirements,files=records,
            pythonRuntime=package_launcher.PYTHON_RUNTIME,
            adapterSourceSha256=sha(source/'LauncherExtractor.cs'))))
        target=self.root/'stripped-bundle'
        with patch.object(package_launcher,'ROOT',source):
            preserved=package_launcher.reuse_runtime(target,original,requirements)
        self.assertEqual(set(preserved),set(keep))
        for name in keep:
            self.assertEqual(sha(target/name),records[name])
        for name in remove:
            self.assertFalse((target/name).exists())
        manifest_path=original/'bundle-manifest.json'
        manifest=json.loads(manifest_path.read_text())
        manifest['pythonRuntime']={**package_launcher.PYTHON_RUNTIME,'version':'3.11.9'}
        manifest_path.write_text(json.dumps(manifest))
        with patch.object(package_launcher,'ROOT',source):
            with self.assertRaisesRegex(ValueError,'Python runtime'):
                package_launcher.reuse_runtime(self.root/'old-python',original,requirements)
        manifest['pythonRuntime']=package_launcher.PYTHON_RUNTIME
        manifest_path.write_text(json.dumps(manifest))
        (original/'.builder/python/python.exe').write_bytes(b'corrupt runtime')
        with patch.object(package_launcher,'ROOT',source):
            with self.assertRaisesRegex(ValueError,'checksum'):
                package_launcher.reuse_runtime(self.root/'corrupt-copy',original,requirements)

    def test_save_and_blender_files_cannot_enter_launcher_archive(self):
        for name in ('models/Jak.blend','unexpected/progress.sav'):
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'game asset'):
                package_launcher.safe_name(name)


if __name__=='__main__':unittest.main()
