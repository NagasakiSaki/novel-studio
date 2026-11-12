"""Isolated full source regression; no live model traffic or author profile.

Use a fresh output path. Raw traces remain local; publish only reviewed metadata.
The network/process guard does not replace any product permission checks.
"""
import argparse
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--tests', nargs='*')
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists():
        raise ValueError('Use a new report path')
    # Synthetic books must also be outside the plugin checkout. Do not relax
    # workspace_admin's plugin/project separation to accommodate a test runner.
    profile = output.parent / ('profile-' + uuid.uuid4().hex)
    if profile.is_relative_to(ROOT):
        raise ValueError('Test output must be outside the source checkout')
    profile.mkdir(parents=True)
    for name in ('Local', 'Roaming', 'Temp', 'Home'):
        (profile / name).mkdir()
    os.environ.update(LOCALAPPDATA=str(profile / 'Local'), APPDATA=str(profile / 'Roaming'),
                      USERPROFILE=str(profile / 'Home'), TEMP=str(profile / 'Temp'), TMP=str(profile / 'Temp'),
                      QT_QPA_PLATFORM='offscreen', PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    for name in list(os.environ):
        if any(part in name.upper() for part in ('API_KEY', 'ACCESS_TOKEN', 'AUTH_TOKEN', 'SECRET_KEY')):
            os.environ.pop(name, None)
    tempfile.tempdir = str(profile / 'Temp')
    sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
    blocked = []
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_popen = subprocess.Popen

    def no_network(*unused, **kwargs):
        blocked.append('network')
        raise AssertionError('RC1 suite refuses live network requests')

    class Process(original_popen):
        # asyncio's Windows transport subclasses Popen during import.
        def __init__(self, argv, *a, **kw):
            command = [str(v) for v in argv] if isinstance(argv, (list, tuple)) else [str(argv)]
            if Path(command[0]).name.lower() in {'agy', 'agy.exe'} and command[1:] not in [['--version'], ['--help']]:
                blocked.append('live_cli')
                raise AssertionError('RC1 suite refuses live model/account CLI operations')
            super().__init__(argv, *a, **kw)

    socket.socket.connect = socket.socket.connect_ex = no_network
    subprocess.Popen = Process
    progress = output.with_suffix('.progress.txt').open('x', encoding='utf-8')
    stream = io.StringIO()
    cases = []

    class Result(unittest.TextTestResult):
        def startTest(self, test):
            progress.write('START ' + test.id() + '\n'); progress.flush()
            super().startTest(test)

        def stopTest(self, test):
            progress.write('END ' + test.id() + '\n'); progress.flush()
            super().stopTest(test)

        def addSuccess(self, test):
            cases.append({'test': test.id(), 'status': 'PASS'})
            super().addSuccess(test)

        def addSkip(self, test, reason):
            cases.append({'test': test.id(), 'status': 'SKIPPED', 'reason': reason})
            super().addSkip(test, reason)

        def addFailure(self, test, error):
            cases.append({'test': test.id(), 'status': 'FAIL'})
            super().addFailure(test, error)

        def addError(self, test, error):
            cases.append({'test': test.id(), 'status': 'ERROR'})
            super().addError(test, error)

    started = time.monotonic()
    try:
        suite = (unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in args.tests)
                 if args.tests else unittest.defaultTestLoader.discover(str(ROOT / 'tests')))
        result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            pass
        else:
            if QApplication.instance():
                from novel_studio.desktop.lifecycle import dispose_windows
                dispose_windows(QApplication.instance())
    finally:
        progress.close()
        socket.socket.connect, socket.socket.connect_ex = original_connect, original_connect_ex
        subprocess.Popen = original_popen
    report = {'status': 'PASS' if result.wasSuccessful() else 'FAIL', 'tests': result.testsRun,
              'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
              'passed': result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
              'seconds': round(time.monotonic() - started, 3), 'cases': cases, 'blocked_live_operations': blocked,
              'scope': 'source suite; isolated profile; synthetic files/credentials; SDK memory transports; Qt offscreen; not native GUI or real model qualification',
              'details': stream.getvalue()}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'cases', 'details'}}, ensure_ascii=True))
    return not result.wasSuccessful()


if __name__ == '__main__':
    raise SystemExit(main())
