"""Select pinned native tools without changing the original game inputs."""
from __future__ import annotations

import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parent
PLATFORMS = ('windows-x64', 'macos-arm64')


def host_platform():
    machine = platform.machine().lower()
    if sys.platform == 'win32' and machine in ('amd64', 'x86_64'):
        return 'windows-x64'
    if sys.platform == 'darwin' and machine == 'arm64':
        return 'macos-arm64'
    raise ValueError('This builder supports Windows x64 and Apple Silicon macOS')


def select(config, target=None):
    target = target or host_platform()
    if target == 'windows-x64' and config.get('platform') == target:
        return dict(config)
    if target != 'macos-arm64' or config.get('game', 'jak3') != 'jak3':
        raise ValueError('Unsupported native Mac game')
    profile = json.loads((ROOT/'platforms/macos-arm64.json').read_text())
    if profile['openGoalVersion'] != config['openGoalVersion']:
        raise ValueError('Native tools and game compatibility versions differ')
    regions = profile.pop('gameProfiles', {}).get(config.get('game', 'jak3'))
    result = {**config, **profile}
    if regions is not None:
        result['supportedSerials'] = list(regions)
        result['regionalProfiles'] = {}
        for serial, native in regions.items():
            original = config.get('regionalProfiles', {}).get(serial, {})
            result['regionalProfiles'][serial] = {
                **original, **native,
                'inputs': {**original.get('inputs', {}), **native.get('inputs', {})}}
    return result


def tool_path(tools, name, config):
    if name not in ('extractor', 'goalc', 'gk'):
        raise ValueError('Unsupported OpenGOAL tool')
    target = config.get('platform', 'windows-x64')
    if target not in PLATFORMS:
        raise ValueError('Unsupported native tool platform')
    return Path(tools)/(name+'.exe' if target == 'windows-x64' else name)


def compiler_options(config):
    return ['--instruction-set', 'arm64'] if config.get('platform') == 'macos-arm64' else []
