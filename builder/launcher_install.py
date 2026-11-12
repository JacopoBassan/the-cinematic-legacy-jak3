"""OpenGOAL launcher extractor adapter. Generates all game assets locally."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import uuid
import platform_tools
import runtime_utils as builder
import game_config as games
import install as installer

RUNTIME_FOLDERS = games.runtime_folders()


def selected_config(value=None):
    return games.validate(value) if value is not None else games.load(builder.ROOT)


def locations(mod, iso, game_config=None):
    selected = selected_config(game_config)
    game = selected['game']
    mod = Path(mod).absolute()
    installer.verify_mod_path(mod, selected)
    root = mod.parents[4]
    data = root/'active'/game/'data'
    if Path(iso).resolve() != (data/'iso_data'/game).resolve():
        raise ValueError('The launcher supplied an unexpected game directory.')
    installer.reject_reparse(data)
    return data, root/'active/bin'


def select_tools(root, config):
    """Prefer the installed pinned release, with exact hashes in either location."""
    candidates = (root/'versions/official'/config['openGoalVersion'], root/'active/bin')
    for folder in candidates:
        installer.reject_reparse(folder)
        if all((folder/name).is_file() and builder.sha(folder/name) == digest
               for name, digest in config['tools'].items()):
            return folder
    raise ValueError(f"Install OpenGOAL tooling {config['openGoalVersion']} through Launcher Settings > Versions. "
                     'Then install or rebuild the vanilla game using that version before retrying this mod.')


def verify_bundle(mod, selected):
    manifest = json.loads((mod/'bundle-manifest.json').read_text())
    if manifest.get('schemaVersion') != 1 or manifest.get('gameAssetsIncluded') is not False:
        raise ValueError('Unsupported launcher bundle manifest.')
    if manifest.get('game') != selected['game']:
        raise ValueError('This launcher package belongs to a different game.')
    for name, digest in manifest['files'].items():
        path = games.relative_file(mod, name)
        installer.reject_reparse(path)
        if not path.is_file() or builder.sha(path) != digest:
            raise ValueError(f'Launcher package checksum failed: {name}. Re-import the complete archive.')
    source = '.builder/source/'
    required = {source+'launcher_install.py', source+'game-config.json', source+'game_config.py',
                source+selected['compatibilityFile'], source+selected['builderModule'].replace('.', '/')+'.py',
                platform_tools.tool_path(mod, 'extractor', selected).name}
    if not required.issubset(manifest['files']):
        raise ValueError('Launcher bundle is missing required source files.')
    return manifest


def free_space_check(mod, data, scratch, game_config=None, minimum_bytes=1024**3):
    selected = selected_config(game_config)
    required = max(1024**3, minimum_bytes)
    if not platform_tools.tool_path(mod, 'gk', selected).exists():
        required += sum(p.stat().st_size for name in selected['runtimeFolders']
                        for p in (data/name).rglob('*') if p.is_file())
        required += 128*1024**2
    if shutil.disk_usage(scratch).free < required:
        raise ValueError(f'Free at least {required/1024**3:.1f} GiB on this drive and retry installation.')


def scratch_parent(mod):
    parent = mod.parents[4].parent/'OpenGOAL-Cinematic-Builds'
    installer.reject_reparse(parent)
    parent.mkdir(exist_ok=True)
    return parent


def install_generated(payload, mod, data, tools, job, game_config=None, build_module=None):
    with installer.mod_lock(mod):
        return _install_generated(payload, mod, data, tools, job, game_config, build_module)


def _install_generated(payload, mod, data, tools, job, game_config=None, build_module=None):
    selected = selected_config(game_config)
    installer.verify_mod_path(mod, selected)
    manifest = installer.verify_payload(payload) if game_config is None else installer.verify_payload(payload, selected)
    config = games.compatibility(builder.ROOT, selected)
    runtime_name = platform_tools.tool_path(mod, 'gk', config).name
    compiler_name = platform_tools.tool_path(mod, 'goalc', config).name
    installer.require_game_closed()
    names = ['data', compiler_name, *installer.METADATA_FILES, runtime_name]
    for name in names:
        installer.reject_reparse(mod/name)
    if (mod/runtime_name).exists():
        if game_config is None:
            installer.install(payload, mod, job/'previous-release', data, tools)
        else:
            installer.install(payload, mod, job/'previous-release', data, tools,
                              game_config=selected, build_module=build_module)
        return
    installer.reject_reparse(job)
    if job.stat().st_dev != mod.stat().st_dev:
        raise ValueError('Temporary build and mod must be on the same drive for atomic installation.')
    stage = job/'installation'
    stage.mkdir()
    published = []
    originals = stage/'previous'
    rollback_failed = False
    transaction = mod/'.cinematic-transaction.json'
    transaction_data = json.dumps({'schemaVersion': 1, 'stage': str(stage),
                                   'files': names, 'token': uuid.uuid4().hex}).encode('utf-8')
    try:
        originals.mkdir()
        for name in selected['runtimeFolders']:
            shutil.copytree(data/name, stage/'data'/name)
        for name in (runtime_name, compiler_name):
            shutil.copy2(tools/name, stage/name)
        for entry in manifest['files']:
            target = stage/entry['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(payload/entry['path'], target)
        for entry in manifest['files']:
            if builder.sha(stage/entry['path']) != entry['sha256']:
                raise ValueError('Staged game asset verification failed.')
        if builder.sha(stage/runtime_name) != manifest['runtimeSha256']:
            raise ValueError('Staged runtime verification failed.')
        if game_config is None:
            installer.put_metadata(stage, mod, manifest)
        else:
            installer.put_metadata(stage, mod, manifest, selected)
        if (mod/'data').exists():
            raise ValueError('An incomplete data folder exists. Remove and re-import this mod in the launcher.')
        installer.reject_reparse(transaction)
        if transaction.exists():
            raise ValueError(f'An interrupted update requires recovery. Preserve {transaction} and {stage}.')
        installer.atomic_bytes(transaction, transaction_data)
        for name in names:
            target = mod/name
            installer.reject_reparse(target)
            # Record intent before either rename so interruption cannot strand
            # an original in the disposable stage without a rollback record.
            published.append(name)
            if target.exists():
                os.replace(target, originals/name)
            os.replace(stage/name, target)
    except BaseException as error:
        # An interruption of rollback itself must preserve every remaining
        # original, even if it lands between restoration operations.
        rollback_failed = True
        failures = []
        for name in reversed(published):
            try:
                if (originals/name).exists():
                    os.replace(originals/name, mod/name)
                elif not (stage/name).exists() and (mod/name).exists():
                    os.replace(mod/name, stage/name)
            except BaseException as restore_error:
                restore_error.add_note(f'Could not restore {mod/name}; preserve {stage}')
                failures.append(restore_error)
        if failures:
            details = '; '.join(f'{type(exc).__name__}: {exc}' for exc in [error, *failures])
            raise BaseExceptionGroup(f'Rollback incomplete; preserve {transaction} and {stage}. {details}',
                                     [error, *failures]) from None
        rollback_failed = False
        raise
    finally:
        original_error = sys.exception()
        if not rollback_failed:
            try:
                installer.reject_reparse(transaction)
                if transaction.exists():
                    if transaction.read_bytes() != transaction_data:
                        raise ValueError(f'Transaction marker changed; preserve {transaction} and {stage}')
                    transaction.unlink()
                if (stage.exists() and stage.parent == job and stage.name == 'installation'
                        and stage.resolve().is_relative_to(job.resolve())):
                    shutil.rmtree(stage)
            except BaseException as cleanup_error:
                if original_error is not None:
                    raise BaseExceptionGroup(f'Installation failed ({original_error}); cleanup also failed '
                                             f'({cleanup_error}). Preserve {stage}.',
                                             [original_error, cleanup_error]) from None
                raise


def execute(mod, iso, phase, game_config=None):
    if phase not in ('decompile', 'compile'):
        raise ValueError('Unsupported launcher installation phase.')
    selected = selected_config(game_config)
    if selected.get('platform') == 'linux-x64' and platform_tools.host_platform() != 'linux-x64':
        raise ValueError('Run the Linux installer on Linux x64')
    data, _ = locations(mod, iso, selected)
    verify_bundle(mod, selected)
    module = games.builder(selected)
    config = games.compatibility(builder.ROOT, selected)
    tools = select_tools(mod.parents[4], config)
    installer.require_game_closed()
    print(f"Checking original {selected['displayName'].split(':')[0]} installation and pinned tooling {config['openGoalVersion']}", flush=True)
    # The builder independently verifies inputs at its own boundary. The first
    # launcher phase performs the early check before its compile step is offered.
    if phase == 'decompile':
        module.preflight(data, tools, config)
    parent = scratch_parent(mod)
    free_space_check(mod, data, parent, selected, config.get('minimumFreeBytes', 1024**3))
    if phase == 'decompile':
        print('Inputs verified. Cinematic conversion runs during the next launcher step.', flush=True)
        return
    with installer.mod_lock(mod):
        job = Path(tempfile.mkdtemp(prefix=selected['game']+'-', dir=parent))
        print(f'Build logs: {job}', flush=True)
        try:
            module.build(data, tools, job/'work', job/'payload', config)
            install_generated(job/'payload', mod, data, tools, job, selected, module)
            (job/'installation.json').write_text(json.dumps({'complete':True, 'game':selected['game'], 'mod':str(mod)}, indent=2))
            for name in ('work', 'payload', 'previous-release'):
                folder = job/name
                if name == 'work' and folder.is_dir():
                    logs = job/'logs'
                    logs.mkdir()
                    for p in folder.iterdir():
                        if p.is_file() and p.suffix in ('.log', '.json'):
                            shutil.copy2(p, logs/p.name)
                if folder.exists() and folder.parent == job and folder.resolve().is_relative_to(parent.resolve()):
                    shutil.rmtree(folder)
            print('Cinematic mod installed. Use Play in OpenGOAL Launcher.', flush=True)
        except BaseException:
            print(f'Installation failed. Logs and work retained at {job}', file=sys.stderr, flush=True)
            raise


def install_signal_handlers():
    # The POSIX entry point creates a private session before starting any tools.
    # Normal SIGTERM can therefore stop its children and still run rollback.
    # SIGKILL/power loss cannot execute cleanup; the lock then refuses reuse.
    if sys.platform in ('darwin', 'linux') and os.getpgrp() == os.getpid():
        def terminate(signum, frame):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            os.killpg(os.getpgrp(), signal.SIGTERM)
            raise KeyboardInterrupt('Installation cancelled')
        signal.signal(signal.SIGTERM, terminate)


def main():
    install_signal_handlers()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('iso', type=Path)
    parser.add_argument('--folder', action='store_true')
    parser.add_argument('--game', required=True, choices=games.GAMES)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--decompile', action='store_true')
    group.add_argument('--compile', action='store_true')
    args = parser.parse_args()
    selected = games.load(builder.ROOT)
    if args.game != selected['game']:
        raise ValueError(f"Import this archive under OpenGOAL Launcher > {selected['game']} > Mods.")
    if not args.folder:
        raise ValueError('Install the vanilla game in OpenGOAL before installing this mod.')
    mod = Path(__file__).resolve().parents[2]
    execute(mod, args.iso, 'decompile' if args.decompile else 'compile', selected)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'Cinematic installer: {error}', file=sys.stderr, flush=True)
        sys.exit(1)
