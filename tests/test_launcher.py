"""Failure modes: wrong install paths, low disk space, corrupt staging, failed publication,
interrupted rollback, lost recovery state, lock conflicts and orphaned adapter
processes. These retained fixture tests isolate destructive failure injection from live
games."""
import json
import os
import subprocess
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote
import launcher_install as adapter
import install as installer
from runtime_utils import sha

GAME = adapter.games.GAME


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='cinematic-launcher-test-')
        self.root=Path(self.tmp.name).resolve()
        self.mod=self.root/f'OpenGOAL/features/{GAME}/mods/_local/Cinematic Test'
        self.mod.mkdir(parents=True)
        self.data=self.root/f'OpenGOAL/active/{GAME}/data'
        self.tools=self.root/'OpenGOAL/active/bin';self.tools.mkdir(parents=True)
        self.payload=self.root/'payload';self.payload.mkdir()
        self.job=self.root/'job';self.job.mkdir()
        for name in adapter.RUNTIME_FOLDERS:
            folder=self.data/name;folder.mkdir(parents=True)
            (folder/'base.bin').write_bytes(b'original runtime bytes')
        for name in ('gk.exe','goalc.exe'):(self.tools/name).write_bytes(name.encode())
        p=self.payload/f'data/out/{GAME}/fr3/GAME.fr3';p.parent.mkdir(parents=True);p.write_bytes(b'generated locally')
        self.manifest=dict(runtimeSha256=sha(self.tools/'gk.exe'),files=[dict(path=p.relative_to(self.payload).as_posix(),bytes=p.stat().st_size,sha256=sha(p))])
        self.verify=patch.object(installer,'verify_payload',return_value=self.manifest);self.verify.start()
        self.closed=patch.object(installer,'require_game_closed');self.closed.start()
        self.source=self.root/'source';self.source.mkdir()
        selected=adapter.selected_config()
        (self.source/'game-config.json').write_text(json.dumps(selected))
        if selected.get('donorGame'):
            donor = self.root/'OpenGOAL/active'/selected['donorGame']/'data/iso_data'/selected['donorGame']
            donor.mkdir(parents=True)
        image=self.source/selected['thumbnail'];image.parent.mkdir(parents=True)
        image.write_bytes(b'authored thumbnail fixture')
        (self.source/'CREDITS.txt').write_text('The Cinematic Legacy\n@jacopobassan')
        self.public=patch.object(installer,'ROOT',self.source);self.public.start()

    def tearDown(self):
        self.verify.stop();self.closed.stop();self.public.stop();self.tmp.cleanup()

    def stale_catalog(self):
        return dict(name=self.mod.name,source=self.mod.parent.name,
                    displayName='Old display name',description='Old description',
                    authors=['Old author'],websiteUrl='https://example.invalid/old',
                    tags=['old'],supportedGames=[GAME],installed=True,
                    versions=[dict(version='0.2.0',publishedDate='2026-10-01T00:00:00Z',
                                   supportedGames=[GAME],assets={'windows':'https://example.invalid/mod.zip'})],
                    downloadCount=42,externalLink='https://example.invalid/project',
                    coverArtUrl='https://example.invalid/old-cover.png',
                    thumbnailArtUrl='https://example.invalid/old-thumbnail.png',
                    perGameConfig={GAME:dict(displayName='Old override',description='Old override description',
                                              coverArtUrl='https://example.invalid/override.png',
                                              thumbnailArtUrl='https://example.invalid/override-thumb.png',
                                              releaseDate='2026-10-01T00:00:00Z')})

    def assert_current_metadata(self,previous):
        current=json.loads((self.mod/'_metadata.json').read_text())
        self.assertEqual(current['name'],self.mod.name)
        self.assertEqual(current['source'],self.mod.parent.name)
        self.assertEqual(current['displayName'],adapter.selected_config()['displayName'])
        self.assertEqual(current['description'],adapter.selected_config()['description'])
        self.assertEqual(current['authors'],['@jacopobassan'])
        self.assertEqual(current['websiteUrl'],'https://jacopobassan.me')
        self.assertEqual(current['tags'],['graphics','character-models'])
        self.assertEqual(current['supportedGames'],[GAME])
        image='https://asset.localhost/'+quote(str(self.mod/'thumbnail.png'),safe='')
        self.assertEqual(current['coverArtUrl'],image)
        self.assertEqual(current['thumbnailArtUrl'],image)
        for key in ('versions','downloadCount'):
            self.assertEqual(current[key],previous[key])
        self.assertIsNone(current['externalLink'])
        override=current['perGameConfig'][GAME]
        for key in ('displayName','description','coverArtUrl','thumbnailArtUrl'):
            self.assertEqual(override[key],current[key])
        self.assertEqual(override['releaseDate'],previous['perGameConfig'][GAME]['releaseDate'])
        self.assertEqual((self.mod/'thumbnail.png').read_bytes(),b'authored thumbnail fixture')

    def test_launcher_local_path_and_iso_boundary(self):
        iso=self.data/f'iso_data/{GAME}'
        self.assertEqual(adapter.locations(self.mod,iso),(self.data,self.tools))
        installer.verify_mod_path(self.mod)
        with self.assertRaises(ValueError):adapter.locations(self.mod,self.root/'unrelated')
        with self.assertRaises(ValueError):adapter.locations(self.root/'wrong',iso)

    def test_first_install_refreshes_catalog_keeps_routing_adapter_and_originals(self):
        (self.mod/'extractor.exe').write_bytes(b'adapter')
        previous=self.stale_catalog()
        (self.mod/'_metadata.json').write_text(json.dumps(previous))
        adapter.install_generated(self.payload,self.mod,self.data,self.tools,self.job)
        self.assertEqual((self.mod/'extractor.exe').read_bytes(),b'adapter')
        self.assert_current_metadata(previous)
        self.assertEqual((self.mod/f'data/out/{GAME}/fr3/GAME.fr3').read_bytes(),b'generated locally')
        self.assertEqual((self.data/f'out/{GAME}/fr3/base.bin').read_bytes(),b'original runtime bytes')
        self.assertFalse((self.mod/'data/iso_data').exists())
        self.assertFalse((self.job/'installation').exists())
        self.assertTrue((self.mod/'gk.exe').is_file())

    def test_corrupt_staged_asset_does_not_publish_runtime(self):
        self.manifest['files'][0]['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'asset verification'):
            adapter.install_generated(self.payload,self.mod,self.data,self.tools,self.job)
        self.assertFalse((self.mod/'gk.exe').exists())
        self.assertFalse((self.mod/'data').exists())
        self.assertFalse((self.job/'installation').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows path length boundary')
    def test_first_install_uses_short_job_stage_for_deep_valid_destination(self):
        shader = 'data/game/graphics/opengl_renderer/shaders/merc2.vert'
        padding = max(0, 248-len(str(self.mod/shader)))
        mod = self.mod.with_name(self.mod.name+'x'*padding)
        mod.mkdir(exist_ok=True)
        self.assertLess(len(str(mod/shader)), 260)
        self.assertGreater(len(str(mod/('.cinematic-install-'+'x'*32)/shader)), 260)
        baseline = self.data/'game/graphics/opengl_renderer/shaders/merc2.vert'
        baseline.parent.mkdir(parents=True)
        baseline.write_bytes(b'original shader bytes')
        adapter.install_generated(self.payload, mod, self.data, self.tools, self.job)
        self.assertEqual((mod/shader).read_bytes(), b'original shader bytes')
        self.assertTrue((mod/'gk.exe').is_file())
        self.assertFalse((self.job/'installation').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows junction boundary')
    def test_first_install_rejects_linked_job_directory(self):
        import _winapi
        external = self.root/'external-job'; external.mkdir()
        (external/'sentinel').write_bytes(b'unchanged')
        self.job.rmdir()
        _winapi.CreateJunction(str(external), str(self.job))
        try:
            with self.assertRaisesRegex(ValueError, 'links or junctions'):
                adapter.install_generated(self.payload, self.mod, self.data, self.tools, self.job)
            self.assertEqual((external/'sentinel').read_bytes(), b'unchanged')
            self.assertFalse((external/'installation').exists())
            self.assertFalse((self.mod/'data').exists())
        finally:
            os.rmdir(self.job)

    def test_low_space_refused_before_copy(self):
        with patch.object(adapter.shutil,'disk_usage',return_value=type('Disk',(),{'free':1})()):
            with self.assertRaisesRegex(ValueError,'Free at least'):
                adapter.free_space_check(self.mod,self.data,self.root)
        self.assertFalse((self.mod/'data').exists())

    def test_publish_failure_rolls_back_and_can_retry(self):
        previous=self.stale_catalog()
        before=(json.dumps(previous,indent=3)+'\n').encode()
        (self.mod/'_metadata.json').write_bytes(before)
        put_metadata=installer.put_metadata
        def fail_after_metadata(*args,**kwargs):
            put_metadata(*args,**kwargs)
            raise OSError('disk full')
        with patch.object(installer,'put_metadata',side_effect=fail_after_metadata):
            with self.assertRaisesRegex(OSError,'disk full'):
                adapter.install_generated(self.payload,self.mod,self.data,self.tools,self.job)
        self.assertFalse((self.mod/'data').exists())
        self.assertFalse((self.mod/'gk.exe').exists())
        self.assertEqual((self.mod/'_metadata.json').read_bytes(),before)
        adapter.install_generated(self.payload,self.mod,self.data,self.tools,self.job)
        self.assertTrue((self.mod/'gk.exe').exists())
        self.assert_current_metadata(previous)

    def test_late_first_install_failure_restores_every_publication_target(self):
        names = ('_metadata.json', 'cinematic-release.json', 'thumbnail.png', 'CREDITS.txt', 'goalc.exe')
        for name in names:
            (self.mod/name).write_bytes(('original '+name).encode())
        before = {name: (self.mod/name).read_bytes() for name in names}
        replace = adapter.os.replace
        def fail_runtime(source, target):
            if target == self.mod/'gk.exe':
                raise OSError('runtime publication failed')
            return replace(source, target)
        with patch.object(adapter.os, 'replace', side_effect=fail_runtime):
            with self.assertRaisesRegex(OSError, 'runtime publication failed'):
                adapter.install_generated(self.payload, self.mod, self.data, self.tools, self.job)
        self.assertEqual({name: (self.mod/name).read_bytes() for name in names}, before)
        self.assertFalse((self.mod/'data').exists())
        self.assertFalse((self.mod/'.cinematic-transaction.json').exists())
        self.assertFalse((self.job/'installation').exists())

    def test_interrupt_after_original_move_restores_catalog(self):
        before = json.dumps(self.stale_catalog()).encode()
        (self.mod/'_metadata.json').write_bytes(before)
        replace = adapter.os.replace
        def interrupt_after_move(source, target):
            result = replace(source, target)
            if source == self.mod/'_metadata.json':
                raise KeyboardInterrupt('interrupt after preserving original')
            return result
        with patch.object(adapter.os, 'replace', side_effect=interrupt_after_move):
            with self.assertRaises(KeyboardInterrupt):
                adapter.install_generated(self.payload, self.mod, self.data, self.tools, self.job)
        self.assertEqual((self.mod/'_metadata.json').read_bytes(), before)
        self.assertFalse((self.mod/'data').exists())
        self.assertFalse((self.mod/'.cinematic-transaction.json').exists())
        self.assertFalse((self.job/'installation').exists())

    def test_interrupted_first_install_rollback_retains_original_and_marker(self):
        before = json.dumps(self.stale_catalog()).encode()
        (self.mod/'_metadata.json').write_bytes(before)
        replace = adapter.os.replace
        def fail_publish_and_restore(source, target):
            if target == self.mod/'gk.exe':
                raise OSError('publish failed')
            if Path(source).parent.name == 'previous' and Path(source).name == '_metadata.json':
                raise KeyboardInterrupt('rollback interrupted')
            return replace(source, target)
        with patch.object(adapter.os, 'replace', side_effect=fail_publish_and_restore):
            with self.assertRaises(BaseExceptionGroup) as caught:
                adapter.install_generated(self.payload, self.mod, self.data, self.tools, self.job)
        self.assertIn('publish failed', str(caught.exception))
        self.assertIn('rollback interrupted', str(caught.exception))
        stage = self.job/'installation'
        self.assertTrue(stage.is_dir())
        self.assertEqual((stage/'previous/_metadata.json').read_bytes(), before)
        self.assertTrue((self.mod/'.cinematic-transaction.json').is_file())
        self.assertFalse((self.mod/'data').exists())
        with self.assertRaisesRegex(ValueError, 'requires recovery'):
            adapter.install_generated(self.payload, self.mod, self.data, self.tools, self.job)

    def test_cleanup_error_keeps_original_publication_error_visible(self):
        replace = adapter.os.replace
        unlink = Path.unlink
        def fail_runtime(source, target):
            if target == self.mod/'gk.exe':
                raise OSError('original publish failure')
            return replace(source, target)
        def fail_marker_cleanup(path, *args, **kwargs):
            if path == self.mod/'.cinematic-transaction.json':
                raise OSError('marker cleanup failure')
            return unlink(path, *args, **kwargs)
        with patch.object(adapter.os, 'replace', side_effect=fail_runtime), \
             patch.object(Path, 'unlink', fail_marker_cleanup):
            with self.assertRaises(BaseExceptionGroup) as caught:
                adapter.install_generated(self.payload, self.mod, self.data, self.tools, self.job)
        self.assertIn('original publish failure', str(caught.exception))
        self.assertIn('marker cleanup failure', str(caught.exception))
        self.assertTrue((self.mod/'.cinematic-transaction.json').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows junction boundary')
    def test_first_install_rejects_metadata_junction_before_staging(self):
        import _winapi
        external = self.root/'external'; external.mkdir()
        (external/'sentinel').write_bytes(b'unchanged')
        link = self.mod/'thumbnail.png'
        _winapi.CreateJunction(str(external), str(link))
        try:
            with self.assertRaisesRegex(ValueError, 'links or junctions'):
                adapter.install_generated(self.payload, self.mod, self.data, self.tools, self.job)
            self.assertEqual((external/'sentinel').read_bytes(), b'unchanged')
            self.assertFalse((self.job/'installation').exists())
            self.assertFalse((self.mod/'data').exists())
        finally:
            os.rmdir(link)

    def execute_environment(self, module):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(adapter, 'verify_bundle'))
        stack.enter_context(patch.object(adapter.games, 'builder', return_value=module))
        stack.enter_context(patch.object(adapter.games, 'compatibility', return_value={
            'platform': 'windows-x64', 'openGoalVersion': 'fixture'}))
        stack.enter_context(patch.object(adapter, 'select_tools', return_value=self.tools))
        stack.enter_context(patch.object(adapter, 'free_space_check'))
        return stack

    def test_compile_lock_conflict_creates_no_job_and_skips_extra_preflight(self):
        (self.mod/'.cinematic-install.lock').write_text('another installer')
        module = Mock()
        with self.execute_environment(module), patch.object(adapter.tempfile, 'mkdtemp') as mkdtemp:
            with self.assertRaisesRegex(ValueError, 'Another install'):
                adapter.execute(self.mod, self.data/f'iso_data/{GAME}', 'compile')
        mkdtemp.assert_not_called()
        module.preflight.assert_not_called()
        module.build.assert_not_called()

    def test_decompile_still_performs_full_early_preflight(self):
        module = Mock()
        with self.execute_environment(module):
            adapter.execute(self.mod, self.data/f'iso_data/{GAME}', 'decompile')
        module.preflight.assert_called_once()
        module.build.assert_not_called()


    def test_mac_sigterm_forwards_to_private_group_before_rollback(self):
        handlers = {}
        def remember(signum, handler):
            handlers[signum] = handler
        with patch.object(adapter.sys, 'platform', 'darwin'), \
             patch.object(adapter.os, 'getpgrp', return_value=123, create=True), \
             patch.object(adapter.os, 'getpid', return_value=123), \
             patch.object(adapter.os, 'killpg', create=True) as killpg, \
             patch.object(adapter.signal, 'signal', side_effect=remember):
            adapter.install_signal_handlers()
            handler = handlers[adapter.signal.SIGTERM]
            with self.assertRaises(KeyboardInterrupt):
                handler(adapter.signal.SIGTERM, None)
            killpg.assert_called_once_with(123, adapter.signal.SIGTERM)
            self.assertEqual(handlers[adapter.signal.SIGTERM], adapter.signal.SIG_IGN)

    def test_success_keeps_logs_and_removes_generated_and_rollback_copies(self):
        module = Mock()
        def build(data, tools, work, payload, config):
            work.mkdir(); payload.mkdir()
            (work/'build.log').write_text('fixture log')
        module.build.side_effect = build
        def publish(payload, mod, data, tools, job, *args):
            previous = job/'previous-release/new-files'; previous.mkdir(parents=True)
            (previous/'asset.bin').write_bytes(b'redundant payload')
        with self.execute_environment(module), patch.object(adapter, 'install_generated', side_effect=publish):
            adapter.execute(self.mod, self.data/f'iso_data/{GAME}', 'compile')
        jobs = list((self.root/'OpenGOAL-Cinematic-Builds').iterdir())
        self.assertEqual(len(jobs), 1)
        self.assertEqual({p.name for p in jobs[0].iterdir()}, {'logs', 'installation.json'})
        self.assertEqual((jobs[0]/'logs/build.log').read_text(), 'fixture log')
        module.preflight.assert_not_called()
        self.assertFalse((self.mod/'.cinematic-install.lock').exists())

    def test_failed_build_keeps_work_and_releases_lock(self):
        module = Mock()
        def build(data, tools, work, payload, config):
            work.mkdir(); payload.mkdir()
            (work/'failure.log').write_text('diagnostic evidence')
            raise RuntimeError('conversion failed')
        module.build.side_effect = build
        with self.execute_environment(module):
            with self.assertRaisesRegex(RuntimeError, 'conversion failed'):
                adapter.execute(self.mod, self.data/f'iso_data/{GAME}', 'compile')
        job = next((self.root/'OpenGOAL-Cinematic-Builds').iterdir())
        self.assertEqual((job/'work/failure.log').read_text(), 'diagnostic evidence')
        self.assertTrue((job/'payload').is_dir())
        self.assertFalse((self.mod/'.cinematic-install.lock').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Object ownership')
    def test_windows_adapter_termination_stops_child_and_grandchild(self):
        import ctypes
        from ctypes import wintypes
        compiler = Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.is_file():
            self.skipTest('C# compiler unavailable')
        root = self.root/'adapter with spaces'; root.mkdir()
        python = root/'.builder/python/python.exe'; python.parent.mkdir(parents=True)
        source = root/'.builder/source/launcher_install.py'; source.parent.mkdir()
        source.write_text('# synthetic child does not execute Python')
        fake = root/'fixture.cs'
        fake.write_text('''using System; using System.Diagnostics; using System.IO; using System.Threading;
class Fixture {
 public static void Main(string[] args) {
  string home = AppDomain.CurrentDomain.BaseDirectory;
  bool grandchild = args.Length == 1 && args[0] == "--grandchild";
  var process = Process.GetCurrentProcess();
  File.WriteAllText(Path.Combine(home, grandchild ? "grandchild.pid" : "child.pid"), process.Id.ToString()+"\\n"+process.StartTime.ToUniversalTime().ToString("o")+"\\n"+Environment.CommandLine);
  if (!grandchild) {
   Console.WriteLine("fixture stdout"); Console.Error.WriteLine("fixture stderr");
   var info = new ProcessStartInfo(System.Reflection.Assembly.GetExecutingAssembly().Location, "--grandchild");
   info.UseShellExecute = false; info.CreateNoWindow = true; Process.Start(info);
  }
  Thread.Sleep(30000);
 }
}''')
        adapter_source = Path(adapter.__file__).with_name('LauncherExtractor.cs')
        wrapper = root/'extractor.exe'
        owned_processes = []
        for src, output in ((fake, python), (adapter_source, wrapper)):
            command = [str(compiler), '/nologo', '/target:exe', '/platform:x64', '/out:'+str(output), str(src)]
            compiler_process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            owned_processes.append({'pid': compiler_process.pid, 'createdAt': time.time(),
                                    'command': command, 'purpose': 'compile adapter fixture', 'port': None})
            try:
                stdout, stderr = compiler_process.communicate(timeout=30)
                self.assertEqual(compiler_process.returncode, 0, (stdout+stderr).decode(errors='replace'))
            finally:
                if compiler_process.poll() is None:
                    compiler_process.kill(); compiler_process.wait(timeout=10)
                compiler_process.stdout.close(); compiler_process.stderr.close()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handles = []
        child = subprocess.Popen([str(wrapper), 'path with spaces', 'embedded"quote'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        owned_processes.append({'pid': child.pid, 'createdAt': time.time(), 'command': child.args,
                                'purpose': 'adapter ownership fixture', 'port': None})
        try:
            deadline = time.monotonic()+10
            markers = [python.parent/'child.pid', python.parent/'grandchild.pid']
            while not all(p.is_file() and p.stat().st_size for p in markers):
                if child.poll() is not None or time.monotonic() > deadline:
                    self.fail('Synthetic adapter descendants did not start')
                time.sleep(.02)
            for marker in markers:
                pid, created, command = marker.read_text().splitlines()
                owned_processes.append({'pid': int(pid), 'createdUtc': created,
                                        'command': command, 'purpose': 'adapter ownership fixture', 'port': None})
                handle = kernel.OpenProcess(0x100001, False, int(pid))
                self.assertTrue(handle, 'Could not track owned fixture descendant')
                handles.append(handle)
            child.kill()
            stdout, stderr = child.communicate(timeout=10)
            self.assertIn(b'fixture stdout', stdout)
            self.assertIn(b'fixture stderr', stderr)
            for handle in handles:
                self.assertEqual(kernel.WaitForSingleObject(handle, 10000), 0,
                                 'Job left a fixture descendant running')
        finally:
            if child.poll() is None:
                child.kill(); child.wait(timeout=10)
            for handle in handles:
                kernel.TerminateProcess(handle, 1)
                kernel.WaitForSingleObject(handle, 10000)
                kernel.CloseHandle(handle)
            if child.stdout: child.stdout.close()
            if child.stderr: child.stderr.close()

    def test_existing_install_uses_transactional_updater(self):
        (self.mod/'gk.exe').write_bytes(b'gk.exe')
        previous=self.stale_catalog()
        (self.mod/'_metadata.json').write_text(json.dumps(previous))
        (self.mod/'saved-progress.bin').write_bytes(b'untouched progress')
        adapter.install_generated(self.payload,self.mod,self.data,self.tools,self.job)
        self.assert_current_metadata(previous)
        self.assertEqual((self.mod/'saved-progress.bin').read_bytes(),b'untouched progress')
        self.assertTrue((self.job/'previous-release/rollback.json').is_file())

    def test_update_failure_restores_exact_catalog_and_progress(self):
        (self.mod/'gk.exe').write_bytes(b'gk.exe')
        before=(json.dumps(self.stale_catalog(),indent=3)+'\n').encode()
        (self.mod/'_metadata.json').write_bytes(before)
        (self.mod/'saved-progress.bin').write_bytes(b'untouched progress')
        replace=installer.os.replace
        failed = False
        def fail_metadata(source,target):
            nonlocal failed
            if target==self.mod/'_metadata.json' and not failed:
                failed = True
                raise OSError('synthetic metadata publish failure')
            return replace(source,target)
        with patch.object(installer.os,'replace',side_effect=fail_metadata):
            with self.assertRaisesRegex(OSError,'metadata publish failure'):
                adapter.install_generated(self.payload,self.mod,self.data,self.tools,self.job)
        self.assertEqual((self.mod/'_metadata.json').read_bytes(),before)
        self.assertEqual((self.mod/'saved-progress.bin').read_bytes(),b'untouched progress')



if __name__=='__main__':unittest.main()
