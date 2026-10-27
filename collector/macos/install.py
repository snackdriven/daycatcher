"""Current-user, local-only launchd installation. No network or desktop control."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

LABEL = 'com.snackdriven.conversation-collector'


def regular(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError('Expected a regular file: ' + str(path))
    return path


def paths(home):
    home = home.expanduser().absolute()
    base = home / 'Library' / 'Application Support' / 'ConversationCollector'
    # Reject symlinks at every existing component before writing private material.
    for target in (base / 'code' / 'macos', base / 'code' / 'journal_collector',
                   base / 'runtime', home / 'Library' / 'LaunchAgents'):
        for parent in (target, *target.parents):
            if parent.is_symlink():
                raise ValueError('Symlink installation paths are not supported')
    return {'base': base, 'code': base / 'code', 'data': base / 'runtime',
            'config': base / 'runtime' / 'collector.json',
            'plist': home / 'Library' / 'LaunchAgents' / (LABEL + '.plist')}


def find_python(explicit=None):
    candidates = [explicit] if explicit else [sys.executable, '/opt/homebrew/bin/python3',
        '/usr/local/bin/python3', shutil.which('python3')]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_absolute() or not Path(candidate).is_file():
            continue
        try:
            result = subprocess.run([str(candidate), '-c',
                'import sys; print(int(sys.version_info >= (3, 11)))'], capture_output=True,
                text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip() == '1':
                # Keep a tested stable Homebrew symlink, rather than pinning
                # launchd to a versioned Cellar directory removed on upgrade.
                return str(Path(candidate).absolute())
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise ValueError('Python 3.11+ is required. Install it first, then pass --python /absolute/path/python3')


def write_file(path, value, *, binary=False):
    regular(path)
    temporary = regular(path.with_name(path.name + '.tmp'))
    with temporary.open('wb' if binary else 'w', **({} if binary else {'encoding': 'utf-8'})) as stream:
        stream.write(value)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def plist_value(p, python, *, keep_alive=True):
    return {'Label': LABEL,
        'ProgramArguments': [python, '-m', 'journal_collector', 'watch', '--config', str(p['config'])],
        'WorkingDirectory': str(p['code']),
        'EnvironmentVariables': {'PYTHONPATH': str(p['code']), 'PYTHONUNBUFFERED': '1'},
        'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False} if keep_alive else False,
        'ThrottleInterval': 30, 'ExitTimeOut': 30, 'Umask': 0o077,
        'StandardOutPath': str(p['data'] / 'launchd.stdout.log'),
        'StandardErrorPath': str(p['data'] / 'launchd.stderr.log')}


def save_plist(p, python, *, keep_alive=True):
    write_file(p['plist'], plistlib.dumps(plist_value(p, python, keep_alive=keep_alive)), binary=True)


def validate_config(p):
    config = json.loads(regular(p['config']).read_text(encoding='utf-8'))
    if not isinstance(config, dict) or not isinstance(config.get('local'), dict) or not isinstance(config.get('claude', {}), dict):
        raise ValueError('Existing configuration has invalid structure; left unchanged')
    if config.get('data_dir') != str(p['data']) or config.get('db') != str(p['data'] / 'collector.sqlite3'):
        raise ValueError('Existing configuration points outside this private runtime; left unchanged')
    if config.get('claude', {}).get('enabled'):
        raise ValueError('This installer accepts local sources only; existing configuration left unchanged')
    if not config.get('local', {}).get('since'):
        raise ValueError('Existing configuration needs a fixed local.since; left unchanged')
    return config


def prepare(p, python, source=None):
    source = source or Path(__file__).resolve().parents[1]
    for directory in (p['base'], p['code'], p['code'] / 'journal_collector', p['code'] / 'macos', p['data']):
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    p['plist'].parent.mkdir(parents=True, exist_ok=True)
    if p['config'].exists():
        validate_config(p)
    package = source / 'journal_collector'
    files = sorted(package.glob('*.py'))
    if not files or not (package / '__main__.py').is_file():
        raise ValueError('Unpack the complete collector bundle before installation')
    for original in [*files, source / 'macos' / 'install.py']:
        regular(original)
        destination = p['code'] / original.parent.name / original.name
        write_file(destination, original.read_bytes(), binary=True)
    if not p['config'].exists():
        config = {'version': 1, 'data_dir': str(p['data']), 'db': str(p['data'] / 'collector.sqlite3'),
            'poll_seconds': 15, 'claude': {'enabled': False},
            'local': {'codex_roots': None, 'claude_roots': None,
                'since': (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),
                'max_bytes': 16777216}}
        write_file(p['config'], json.dumps(config, indent=2) + '\n')
    save_plist(p, python)
    return {'prepared': True, 'config': str(p['config']), 'code': str(p['code']),
            'plist': str(p['plist']), 'live_verified': False}


def launchctl(*args, check=True):
    result = subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True, timeout=45)
    if check and result.returncode:
        raise RuntimeError('launchctl failed: ' + ' '.join(args[:1]) + '; check local launchd status')
    return result


def loaded(domain):
    return launchctl('print', domain + '/' + LABEL, check=False).returncode == 0


def worker_locked(data):
    lock = regular(data / 'local-worker.lock')
    if not lock.exists():
        return False
    # This is the same POSIX advisory lock used by journal_collector.runtime.
    import fcntl
    with lock.open('r+b') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def stop(p, domain):
    regular(p['plist'])
    if p['plist'].exists():
        value = plistlib.loads(p['plist'].read_bytes())
        if value.get('Label') != LABEL:
            raise ValueError('Unrecognized launchd definition')
        value['KeepAlive'] = False
        write_file(p['plist'], plistlib.dumps(value), binary=True)
    # Clean exit is never restarted by SuccessfulExit:false. Bootout requests
    # removal, but launchd can remain loaded briefly while a scan finishes.
    if p['data'].exists():
        write_file(p['data'] / 'STOP', 'Requested by macOS collector manager\n')
    if loaded(domain):
        launchctl('bootout', domain + '/' + LABEL)
    deadline = time.monotonic() + 35
    while True:
        still_loaded = loaded(domain)
        still_locked = worker_locked(p['data'])
        if not still_loaded and not still_locked:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('Stop requested but stop not confirmed within 35 seconds; '
                f'launchd_loaded={still_loaded}, worker_locked={still_locked}; inspect local status')
        time.sleep(0.1)
    return {'stopped': True, 'launchd_loaded': False}


def start(p, python, domain):
    validate_config(p)
    is_loaded = loaded(domain)
    if (p['data'] / 'STOP').exists() and (is_loaded or worker_locked(p['data'])):
        # launchd can unload before its worker releases the lock. Keep STOP in
        # place until both finish, so the old worker cannot resume unexpectedly.
        stop(p, domain)
    elif is_loaded:
        return {'launchd_loaded': True, 'already_loaded': True, 'capture_verified': False}
    regular(p['data'] / 'STOP').unlink(missing_ok=True)
    save_plist(p, python)
    launchctl('enable', domain + '/' + LABEL)
    launchctl('bootstrap', domain, str(p['plist']))
    if not loaded(domain):
        raise RuntimeError('launchd job registration was not confirmed')
    return {'launchd_loaded': True, 'capture_verified': False, 'detail': 'Use status to inspect source coverage'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'start', 'stop', 'status', 'uninstall'])
    parser.add_argument('--python')
    parser.add_argument('--prepare-only', action='store_true', help='Stage files without running launchctl or collection')
    parser.add_argument('--home', type=Path, default=Path.home(), help='Home override for preparation tests only')
    args = parser.parse_args(argv)
    try:
        if args.prepare_only and args.action != 'install':
            raise ValueError('--prepare-only applies only to install')
        if not args.prepare_only and (sys.platform != 'darwin' or args.home != Path.home()):
            raise ValueError('Live actions require macOS and the current user home; use install --prepare-only for portable checks')
        os.umask(0o077)
        p = paths(args.home)
        if args.action == 'install':
            python = find_python(args.python)
            # Refuse replacing code beneath a live worker. Reinstall after stop.
            if not args.prepare_only and loaded('gui/' + str(os.getuid())):
                raise ValueError('Collector is loaded; run stop before reinstalling')
            result = prepare(p, python)
            if not args.prepare_only:
                result.update(start(p, python, 'gui/' + str(os.getuid())))
        else:
            domain = 'gui/' + str(os.getuid())
            if args.action in ('stop', 'uninstall'):
                result = stop(p, domain)
                if args.action == 'uninstall':
                    regular(p['plist']).unlink(missing_ok=True)
                    result.update({'uninstalled': True, 'retained_private_files': str(p['base'])})
            elif args.action == 'start':
                result = start(p, find_python(args.python), domain)
            else:
                validate_config(p)
                completed = subprocess.run([find_python(args.python), '-m', 'journal_collector', 'status',
                    '--config', str(p['config'])], cwd=p['code'], capture_output=True, text=True, timeout=30)
                if completed.returncode:
                    raise RuntimeError('Collector status failed; inspect local logs')
                result = json.loads(completed.stdout)
                result['launchd_loaded'] = loaded(domain)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'error': type(error).__name__, 'detail': str(error)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
