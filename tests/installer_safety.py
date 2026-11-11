"""Failure modes: partial writes, corrupt inputs or backups, unsafe paths, hardlinks,
junctions, lock bypass, failed rollback and Windows path limits. All 13 existing
filesystem fixtures remain selected-game tests with disposable data."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import install as installer
import game_config as games

GAME = games.GAME


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): digest(p)
            for p in folder.rglob('*') if p.is_file()}


class Fixture:
    def __init__(self, root, existing=True):
        self.root = root
        root.mkdir(parents=True)
        self.public = root/'builder'
        self.public.mkdir()
        self.selected = games.default_config()
        (self.public/'game-config.json').write_text(json.dumps(self.selected))
        descriptor = self.public/'game-configs'/f'{GAME}.json'
        descriptor.parent.mkdir()
        descriptor.write_text(json.dumps(self.selected))
        self.payload = root/'payload'
        self.payload.mkdir()
        self.mod = root/f'OpenGOAL/features/{GAME}/mods/example/Cinematic Models'
        self.backup = root/'previous-release'
        self.runtime = b'synthetic supported runtime, never executable'
        self.config = {'game': GAME, 'version': 'test-only', 'openGoalVersion': 'test-only',
                       'platform': 'windows-x64',
                       'tools': {'gk.exe': hashlib.sha256(self.runtime).hexdigest()}}
        compatibility = self.public/self.selected['compatibilityFile']
        compatibility.parent.mkdir(parents=True, exist_ok=True)
        compatibility.write_text(json.dumps(self.config))
        (self.public/'CREDITS.txt').write_text('Synthetic fixture credits')
        (self.public/'artwork').mkdir()
        (self.public/self.selected['thumbnail']).write_bytes(b'synthetic image')
        self.entries = []
        for relative in (f'data/out/{GAME}/fr3/GAME.fr3',
                         f'data/out/{GAME}/iso/GAME.CGO',
                         f'data/out/{GAME}/fr3/PORT.fr3',
                         'data/game/graphics/opengl_renderer/shaders/merc2.vert'):
            target = self.payload/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(('new '+relative).encode())
            self.entries.append({'path': relative, 'bytes': target.stat().st_size,
                                 'sha256': digest(target)})
        self.config['payloadPaths'] = [entry['path'] for entry in self.entries]
        compatibility = self.public/self.selected['compatibilityFile']
        compatibility.parent.mkdir(parents=True, exist_ok=True)
        compatibility.write_text(json.dumps(self.config))
        self.manifest = dict(self.config, schemaVersion=1,
                             runtimeSha256=self.config['tools']['gk.exe'],
                             files=self.entries)
        self.save_manifest()
        if existing:
            self.mod.mkdir(parents=True)
            (self.mod/'gk.exe').write_bytes(self.runtime)
            for relative in [entry['path'] for entry in self.entries[:2]+self.entries[-1:]]:
                target = self.mod/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(('original '+relative).encode())
            for name in ('_metadata.json', 'thumbnail.png', 'CREDITS.txt'):
                (self.mod/name).write_bytes(('original '+name).encode())
            (self.mod/'settings/saves').mkdir(parents=True)
            (self.mod/'settings/saves/slot.bin').write_bytes(b'untouched saved progress')

    def save_manifest(self):
        (self.payload/'manifest.json').write_text(json.dumps(self.manifest))

    @contextmanager
    def environment(self):
        with patch.object(installer, 'ROOT', self.public), \
             patch.object(installer, 'require_game_closed', lambda: None):
            yield

    def update(self, backup=None):
        with self.environment():
            installer.install(self.payload, self.mod, backup or self.backup)


def expect_rejection(fixture, mutation, expected):
    before = snapshot(fixture.mod)
    mutation()
    try:
        fixture.update()
    except ValueError as error:
        check(expected in str(error), f'Wrong rejection: {error}')
    else:
        raise AssertionError('Invalid installation was accepted')
    check(snapshot(fixture.mod) == before, 'Rejected input changed installed files')
    check(not fixture.backup.exists(), 'Rejected input created a backup or stage')


def test_update(root):
    f = Fixture(root)
    old = snapshot(f.mod)
    f.update()
    for entry in f.entries:
        check(digest(f.mod/entry['path']) == entry['sha256'], 'Updated file mismatch')
    check(digest(f.mod/'settings/saves/slot.bin') == old['settings/saves/slot.bin'], 'Save changed')
    check(digest(f.mod/'gk.exe') == old['gk.exe'], 'Runtime changed')
    records = json.loads((f.backup/'rollback.json').read_text())['files']
    for record in records:
        if record['existed']:
            check(digest(f.backup/record['path']) == old[record['path']], 'Backup mismatch')
    metadata = json.loads((f.mod/'_metadata.json').read_text())
    check(metadata['authors'] == ['@jacopobassan'], 'Author metadata missing')
    check(metadata['websiteUrl'] == 'https://jacopobassan.me', 'Website metadata missing')


def test_mid_update_rollback(root):
    f = Fixture(root)
    old = snapshot(f.mod)
    original_replace = installer.os.replace
    calls = 0
    def fail_fifth(source, target):
        nonlocal calls
        if Path(target).is_relative_to(f.mod) and Path(target).name != '.cinematic-transaction.json':
            calls += 1
            if calls == 5:
                raise OSError('injected replacement failure')
        return original_replace(source, target)
    try:
        with patch.object(installer.os, 'replace', fail_fifth):
            f.update()
    except OSError as error:
        check('injected' in str(error), 'Unexpected failure')
    else:
        raise AssertionError('Failure injection did not run')
    check(snapshot(f.mod) == old, 'Rollback did not restore the complete installation')
    f.update(root/'second-backup')


def test_partial_copy_rollback(root):
    f = Fixture(root)
    old = snapshot(f.mod)
    original_copy = installer.shutil.copyfileobj
    def fail_copy(source, target, *args, **kwargs):
        if Path(source.name) == f.backup/'new-files'/f.entries[1]['path']:
            target.write(b'partial transfer')
            raise OSError('injected partial copy failure')
        return original_copy(source, target, *args, **kwargs)
    try:
        with patch.object(installer.shutil, 'copyfileobj', fail_copy):
            f.update()
    except OSError as error:
        check('injected' in str(error), 'Unexpected failure')
    else:
        raise AssertionError('Failure injection did not run')
    check(snapshot(f.mod) == old, 'Partial copy was not rolled back completely')


def test_interrupt_rollback(root):
    f = Fixture(root)
    old = snapshot(f.mod)
    original_replace = installer.os.replace
    calls = 0
    def interrupt_second(source, target):
        nonlocal calls
        if Path(target).is_relative_to(f.mod) and Path(target).name != '.cinematic-transaction.json':
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt('injected user interruption')
        return original_replace(source, target)
    try:
        with patch.object(installer.os, 'replace', interrupt_second):
            f.update()
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError('Interruption injection did not run')
    check(snapshot(f.mod) == old, 'User interruption left a partially updated installation')


def test_input_rejections(root):
    cases = [
        ('hash', lambda f: (f.payload/f.entries[0]['path']).write_bytes(b'corrupt'), 'checksum'),
        ('version', lambda f: f.manifest.update(version='unsupported'), 'version'),
        ('runtime-manifest', lambda f: f.manifest.update(runtimeSha256='0'*64), 'manifest'),
        ('runtime-installed', lambda f: (f.mod/'gk.exe').write_bytes(b'unsupported runtime'), 'runtime'),
        ('traversal', lambda f: f.entries[0].update(path=f'data/out/{GAME}/fr3/../../outside.fr3'), 'Unsafe'),
        ('duplicate', lambda f: f.entries.append(dict(f.entries[0])), 'Duplicate'),
        ('omitted-file', lambda f: f.entries.pop(), 'file'),
        ('other-shader', lambda f: f.entries[-1].update(path='data/game/graphics/opengl_renderer/shaders/merc2.frag'), 'Unsafe'),
        ('shader-path', lambda f: f.entries[-1].update(path='data/game/assets/shaders/merc2.vert'), 'Unsafe'),
        ('shader-traversal', lambda f: f.entries[-1].update(path='data/game/graphics/opengl_renderer/shaders/../merc2.vert'), 'Unsafe'),
    ]
    for name, mutate, expected in cases:
        f = Fixture(root/name)
        # The installed-runtime case changes the fixture before taking the
        # protected snapshot, since that is the invalid pre-existing state.
        if name == 'runtime-installed':
            mutate(f)
            expect_rejection(f, lambda: None, expected)
        else:
            def mutation():
                mutate(f)
                f.save_manifest()
            expect_rejection(f, mutation, expected)


def test_hardlink_source_preservation(root):
    f = Fixture(root)
    target = f.mod/f.entries[0]['path']
    protected = root/'original-protected.fr3'
    os.link(target, protected)
    old = digest(protected)
    f.update()
    check(digest(protected) == old, 'Updating mod modified a linked original file')
    check(digest(target) == f.entries[0]['sha256'], 'Mod update did not replace hardlink')
    check(not os.path.samefile(target, protected), 'Mod remains linked to the original')


def test_metadata_hardlink_preservation(root):
    f = Fixture(root)
    protected = root/'protected'
    protected.mkdir()
    for name in installer.METADATA_FILES:
        target = f.mod/name
        if not target.exists():
            target.write_bytes(b'original release metadata')
        os.link(target, protected/name)
    old = snapshot(protected)
    with f.environment():
        installer.put_metadata(f.mod, f.mod, f.manifest)
    check(snapshot(protected) == old, 'Metadata publication overwrote an external hardlink')
    check(all(not os.path.samefile(f.mod/name, protected/name) for name in installer.METADATA_FILES),
          'Published metadata remains linked to external files')


def test_failed_rollback_preserves_complete_files_and_continues(root):
    f = Fixture(root)
    old = snapshot(f.mod)
    replace = installer.os.replace
    copy = installer.shutil.copyfileobj
    mkstemp = installer.tempfile.mkstemp
    failure_target = f.mod/f.entries[1]['path']
    attempts = []
    temporaries = []
    def track_temp(*args, **kwargs):
        descriptor, name = mkstemp(*args, **kwargs)
        temporaries.append(Path(name))
        return descriptor, name
    def fail_publish(source, target):
        if Path(target) == f.mod/f.entries[2]['path']:
            raise OSError('forward failure')
        return replace(source, target)
    def fail_restore(source, target, *args, **kwargs):
        source_path = Path(source.name)
        if source_path.is_relative_to(f.backup) and not source_path.is_relative_to(f.backup/'new-files'):
            attempts.append(source_path)
            check(all(p == temporaries[-1] or not p.exists() for p in temporaries),
                  'Forward temporary still consumes rollback space')
            if source_path == f.backup/f.entries[1]['path']:
                target.write(b'partial rollback')
                raise OSError('rollback copy failure')
        return copy(source, target, *args, **kwargs)
    with patch.object(installer.os, 'replace', fail_publish), \
         patch.object(installer.shutil, 'copyfileobj', fail_restore), \
         patch.object(installer.tempfile, 'mkstemp', track_temp):
        try:
            f.update()
        except ExceptionGroup as error:
            check('forward failure' in str(error.exceptions[0]), 'Original failure lost')
            check(any('rollback copy failure' in str(e) for e in error.exceptions[1:]), 'Rollback failure lost')
        else:
            raise AssertionError('Incomplete rollback was not reported')
    check(len(attempts) == 2, 'Rollback stopped before restoring the other touched file')
    check(digest(f.mod/f.entries[0]['path']) == old[f.entries[0]['path']], 'Independent restoration skipped')
    check(digest(failure_target) == f.entries[1]['sha256'], 'Failed rollback truncated the installed file')
    check(digest(f.backup/f.entries[1]['path']) == old[f.entries[1]['path']], 'Verified backup changed')
    check((f.mod/'.cinematic-transaction.json').is_file(), 'Incomplete rollback lost its recovery marker')
    check(all(not p.exists() for p in temporaries), 'Failed rollback temporary remains')
    before = snapshot(f.mod)
    try:
        f.update(root/'retry-backup')
    except ValueError as error:
        check('recovery' in str(error), 'Incomplete update did not explain recovery')
    else:
        raise AssertionError('A new update overwrote an incomplete transaction')
    check(snapshot(f.mod) == before, 'Blocked recovery attempt changed installed files')


def test_rollback_checks_backup_before_replacement(root):
    f = Fixture(root)
    replace = installer.os.replace
    first = f.mod/f.entries[0]['path']
    failed = False
    def corrupt_backup(source, target):
        nonlocal failed
        if not failed and Path(target) == f.mod/f.entries[1]['path']:
            failed = True
            (f.backup/f.entries[0]['path']).write_bytes(b'corrupted backup')
            raise OSError('forward failure')
        return replace(source, target)
    with patch.object(installer.os, 'replace', corrupt_backup):
        try:
            f.update()
        except ExceptionGroup as error:
            check(any('checksum' in str(e) for e in error.exceptions), 'Corrupt backup not reported')
        else:
            raise AssertionError('Corrupt rollback was accepted')
    check(digest(first) == f.entries[0]['sha256'], 'Corrupt backup replaced complete installed bytes')


def test_shared_lock_and_interrupted_transaction(root):
    import threading
    f = Fixture(root)
    with installer.mod_lock(f.mod):
        with installer.mod_lock(f.mod):
            check((f.mod/'.cinematic-install.lock').is_file(), 'Nested lock lost ownership')
        errors = []
        def competitor():
            try:
                with installer.mod_lock(f.mod):
                    errors.append('accepted')
            except ValueError:
                errors.append('blocked')
        thread = threading.Thread(target=competitor)
        thread.start()
        thread.join(timeout=5)
        check(not thread.is_alive() and errors == ['blocked'], 'Another thread bypassed the held lock')
        f.update()
    check(not (f.mod/'.cinematic-install.lock').exists(), 'Completed lock remains')
    lock = f.mod/'.cinematic-install.lock'
    lock.write_text('unverified interrupted owner')
    before = snapshot(f.mod)
    try:
        f.update(root/'second-backup')
    except ValueError as error:
        check('lock' in str(error), 'Existing lock rejection lacks guidance')
    else:
        raise AssertionError('Unverified stale lock was bypassed')
    check(snapshot(f.mod) == before, 'Lock rejection changed the mod')


def test_staged_payload_corruption_is_rejected_before_publish(root):
    f = Fixture(root)
    before = snapshot(f.mod)
    put_metadata = installer.put_metadata
    replace = installer.os.replace
    publications = []
    def corrupt_stage(stage, *args, **kwargs):
        put_metadata(stage, *args, **kwargs)
        (stage/f.entries[0]['path']).write_bytes(b'corrupt staged payload')
    def track_publish(source, target):
        if Path(target) == f.mod/f.entries[0]['path'] and digest(source) != before[f.entries[0]['path']]:
            publications.append(target)
        return replace(source, target)
    with patch.object(installer, 'put_metadata', corrupt_stage), \
         patch.object(installer.os, 'replace', track_publish):
        try:
            f.update()
        except ValueError as error:
            check('checksum' in str(error), 'Staged corruption produced the wrong error')
        else:
            raise AssertionError('Corrupt staged payload was accepted')
    check(not publications, 'Corrupt bytes were published before verification')
    check(snapshot(f.mod) == before, 'Rejected staged corruption changed the installation')


def test_atomic_writes_near_windows_path_limit(root):
    root.mkdir(parents=True)
    # The target is a valid legacy Windows path; deriving a longer temporary
    # filename from it would exceed MAX_PATH even though publication is legal.
    component_length = 244-len(str(root))-1
    check(0 < component_length < 256, 'Fixture root cannot represent the long-path boundary')
    folder = root/('p'*component_length)
    folder.mkdir()
    target = folder/'metadata.json'
    check(len(str(target)) == 258, 'Fixture does not exercise a near-limit target')
    installer.atomic_bytes(target, b'initial metadata')
    check(target.read_bytes() == b'initial metadata', 'Atomic metadata failed at a valid long path')
    source = root/'source.bin'
    source.write_bytes(b'verified replacement')
    installer.atomic_copy(source, target, digest(source))
    check(target.read_bytes() == source.read_bytes(), 'Atomic copy failed at a valid long path')
    check(list(folder.iterdir()) == [target], 'Temporary files remain after long-path publication')


def test_nested_junction(root):
    import _winapi
    f = Fixture(root)
    protected = root/'protected-external-data'
    protected.mkdir()
    (protected/'sentinel.txt').write_text('must remain unchanged')
    # Link a previously absent destination. The existing core paths stay real.
    link = f.mod/f'data/out/{GAME}/fr3/PORT.fr3'
    _winapi.CreateJunction(str(protected), str(link))
    old = snapshot(protected)
    try:
        with f.environment():
            try:
                installer.install(f.payload, f.mod, f.backup)
            except ValueError as error:
                check('links or junctions' in str(error), 'Wrong junction rejection')
            else:
                raise AssertionError('Nested destination junction was accepted')
        check(snapshot(protected) == old, 'Junction target changed')
        check(not f.backup.exists(), 'Junction rejection happened after mutation')
    finally:
        # Removing a directory junction itself never traverses its target.
        os.rmdir(link)


def run_checks(work):
    work = Path(work).resolve()
    if os.name != 'nt':
        raise ValueError('These installer fixtures target Windows')
    if work.exists():
        raise ValueError('Use a new empty test directory')
    work.mkdir(parents=True)
    tests = [test_update, test_mid_update_rollback, test_partial_copy_rollback, test_interrupt_rollback,
             test_input_rejections, test_hardlink_source_preservation,
             test_nested_junction,
             test_metadata_hardlink_preservation, test_failed_rollback_preserves_complete_files_and_continues,
             test_rollback_checks_backup_before_replacement, test_shared_lock_and_interrupted_transaction,
             test_staged_payload_corruption_is_rejected_before_publish,
             test_atomic_writes_near_windows_path_limit]
    results = []
    for index,test in enumerate(tests):
        try:
            # Keep deep Windows shader fixture paths below legacy MAX_PATH.
            test(work/str(index))
        except Exception as error:
            results.append({'test': test.__name__, 'passed': False, 'error': str(error)})
        else:
            results.append({'test': test.__name__, 'passed': True})
    report = {'syntheticFixturesOnly': True, 'gameFilesAccessed': False,
              'results': results, 'passed': all(item['passed'] for item in results)}
    return report
