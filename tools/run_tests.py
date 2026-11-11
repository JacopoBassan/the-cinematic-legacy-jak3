"""Run asset-free regression checks and clean their temporary workspace."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.dont_write_bytecode = True
    game = json.loads((ROOT/'mod-release.json').read_text())['game']
    for folder in (ROOT/'builder', ROOT/'builder/stages', ROOT/'builder'/game, ROOT/'tools', ROOT/'tests'):
        sys.path.insert(0, str(folder))
    previous_temp = tempfile.tempdir
    keys = ('TMP', 'TEMP', 'CINEMATIC_TEST_TEMP')
    previous_environment = {key: os.environ.get(key) for key in keys}
    try:
        with tempfile.TemporaryDirectory(prefix='cl-tests-') as temporary:
            work = Path(temporary)
            tempfile.tempdir = str(work)
            os.environ.update(TMP=str(work), TEMP=str(work), CINEMATIC_TEST_TEMP=str(work))
            suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_*.py')
            result = unittest.TextTestRunner(verbosity=1).run(suite)
            report = {'unitTests': result.testsRun, 'gameAssetsUsed': False,
                      'passed': result.wasSuccessful()}
            if report['passed'] and os.name == 'nt':
                from installer_safety import run_checks
                safety = run_checks(work / 'safety')
                report['installerSafetyFixtures'] = len(safety['results'])
                report['passed'] = safety['passed']
                if not safety['passed']:
                    print(json.dumps(safety, indent=2))
            elif os.name != 'nt':
                report['installerSafetyFixtures'] = 'Windows only'
            print(json.dumps(report, indent=2))
            return 0 if report['passed'] else 1
    finally:
        tempfile.tempdir = previous_temp
        for key, value in previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == '__main__':
    raise SystemExit(main())
