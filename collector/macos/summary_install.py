"""Manage the separate current-user hourly high-level work summary job."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

_spec = importlib.util.spec_from_file_location('collector_mac_install', Path(__file__).with_name('install.py'))
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
regular, write_file, find_python, launchctl = _base.regular, _base.write_file, _base.find_python, _base.launchctl
LABEL = 'com.snackdriven.work-summary-sync'
MODULES = ('journal_collector/work_summary.py', 'journal_collector/summary_publish.py', 'summary_service.py')


def paths(home):
    p = _base.paths(home)
    p['config'] = p['data'] / 'summary.json'
    p['plist'] = home.expanduser().absolute() / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
    p['stop'] = p['data'] / 'summary-STOP'
    for target in (p['data'] / '.summary-worker',):
        for parent in (target, *target.parents):
            if parent.is_symlink():
                raise ValueError('Symlink installation paths are not supported')
    return p


def find_gh(explicit=None):
    candidate = explicit or shutil.which('gh')
    if not candidate or not Path(candidate).is_absolute() or not Path(candidate).is_file():
        raise ValueError('Existing gh executable is required; pass --gh /absolute/path/gh')
    return str(Path(candidate).absolute())


def validate_config(p, *, check_gh=True):
    value = json.loads(regular(p['config']).read_text(encoding='utf-8'))
    expected = {'data_dir': str(p['data']), 'source_db': str(p['data'] / 'collector.sqlite3'),
                'queue_db': str(p['data'] / 'work-summary.sqlite3'),
                'repository': 'snackdriven/day-crumbs', 'branch': 'main'}
    if not isinstance(value, dict) or any(value.get(k) != v for k, v in expected.items()):
        raise ValueError('Summary configuration does not match the private runtime and fixed destination')
    if type(value.get('enabled')) is not bool:
        raise ValueError('Summary enabled must be a boolean')
    if value.get('limit') != 500 or value.get('max_publish') != 20:
        raise ValueError('Summary bounds must be limit=500 and max_publish=20')
    if check_gh:
        find_gh(value.get('gh_path'))
    return value


def save_config(p, config):
    write_file(p['config'], json.dumps(config, indent=2) + '\n')


def plist_value(p, python, *, enabled=False):
    return {'Label': LABEL, 'ProgramArguments': [python, str(p['code'] / 'summary_service.py'),
        '--config', str(p['config'])], 'WorkingDirectory': str(p['code']),
        'EnvironmentVariables': {'PYTHONUNBUFFERED': '1'},
        'RunAtLoad': True, 'StartInterval': 3600, 'Disabled': not enabled,
        'KeepAlive': False, 'ExitTimeOut': 30, 'Umask': 0o077,
        'StandardOutPath': str(p['data'] / 'summary.launchd.stdout.log'),
        'StandardErrorPath': str(p['data'] / 'summary.launchd.stderr.log')}


def save_plist(p, python, *, enabled=False):
    write_file(p['plist'], plistlib.dumps(plist_value(p, python, enabled=enabled)), binary=True)


def prepare(p, python, gh, source=None):
    source = source or Path(__file__).resolve().parents[1]
    originals = [source / name for name in MODULES] + [source / 'macos' / 'summary_install.py']
    for original in originals:
        if not regular(original).is_file():
            raise ValueError('Unpack the complete work-summary bundle before installation')
    # This is an add-on: never install or overwrite collector modules/configuration.
    if not (p['code'] / 'macos' / 'install.py').is_file():
        raise ValueError('Install the existing conversation collector first')
    config = validate_config(p) if p['config'].exists() else {
        'data_dir': str(p['data']), 'source_db': str(p['data'] / 'collector.sqlite3'),
        'queue_db': str(p['data'] / 'work-summary.sqlite3'), 'gh_path': find_gh(gh),
        'enabled': False, 'limit': 500, 'max_publish': 20,
        'repository': 'snackdriven/day-crumbs', 'branch': 'main'}
    for directory in (p['base'], p['code'], p['code'] / 'macos', p['data']):
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    p['plist'].parent.mkdir(parents=True, exist_ok=True)
    for original in originals:
        destination = p['code'] / original.relative_to(source)
        write_file(destination, original.read_bytes(), binary=True)
    config['enabled'] = False
    save_config(p, config)
    write_file(p['stop'], 'Summary delivery disabled until deliberate start\n')
    save_plist(p, python)
    return {'prepared': True, 'enabled': False, 'config': str(p['config']),
            'plist': str(p['plist']), 'live_verified': False}


def loaded(domain):
    return launchctl('print', domain + '/' + LABEL, check=False).returncode == 0


def worker_locked(data):
    return _base.worker_locked(data / '.summary-worker')


def stop(p, domain):
    # A running worker may have loaded a formerly valid config. Stop it even
    # when the file has since become unreadable or corrupt.
    if p['data'].exists():
        write_file(p['stop'], 'Requested by macOS summary manager\n')
    config_repair_required = False
    if p['config'].exists():
        try:
            config = validate_config(p, check_gh=False)
            config['enabled'] = False
            save_config(p, config)
        except (ValueError, OSError):
            config_repair_required = True
    if regular(p['plist']).exists():
        value = plistlib.loads(p['plist'].read_bytes())
        if value.get('Label') != LABEL:
            raise ValueError('Unrecognized summary launchd definition')
        value['Disabled'] = True
        value['KeepAlive'] = False
        write_file(p['plist'], plistlib.dumps(value), binary=True)
    launchctl('disable', domain + '/' + LABEL)
    if loaded(domain):
        launchctl('bootout', domain + '/' + LABEL)
    deadline = time.monotonic() + 35
    while True:
        still_loaded, still_locked = loaded(domain), worker_locked(p['data'])
        if not still_loaded and not still_locked:
            return {'stopped': True, 'enabled': False, 'launchd_loaded': False,
                    'config_repair_required': config_repair_required}
        if time.monotonic() >= deadline:
            raise RuntimeError('Summary stop not confirmed within 35 seconds; '
                f'launchd_loaded={still_loaded}, worker_locked={still_locked}')
        time.sleep(0.1)


def start(p, python, domain):
    config = validate_config(p)
    is_loaded = loaded(domain)
    if (p['stop'].exists() or not config['enabled']) and (is_loaded or worker_locked(p['data'])):
        stop(p, domain)
    elif is_loaded:
        return {'launchd_loaded': True, 'already_loaded': True, 'publish_verified': False}
    elif worker_locked(p['data']):
        raise ValueError('A summary worker is active; wait for completion before starting')
    regular(p['stop']).unlink(missing_ok=True)
    config['enabled'] = True
    save_config(p, config)
    save_plist(p, python, enabled=True)
    launchctl('enable', domain + '/' + LABEL)
    launchctl('bootstrap', domain, str(p['plist']))
    if not loaded(domain):
        raise RuntimeError('Summary launchd registration was not confirmed')
    return {'launchd_loaded': True, 'enabled': True, 'publish_verified': False}


def status(p, domain):
    config = validate_config(p, check_gh=False)
    result = {'enabled': config['enabled'], 'stop_requested': p['stop'].exists(),
              'launchd_loaded': loaded(domain), 'worker_locked': worker_locked(p['data'])}
    status_path = regular(p['data'] / 'summary-status.json')
    if status_path.exists():
        value = json.loads(status_path.read_text(encoding='utf-8'))
        # Counts only: never forward arbitrary strings or nested body/evidence fields.
        keys = ('prepared', 'published', 'queued', 'review_required', 'failed', 'pending', 'delivered', 'scanned')
        if isinstance(value, dict):
            states = {'running', 'idle', 'disabled', 'stopped', 'error', 'preview', 'busy', 'review_required'}
            errors = {'privacy', 'conflict', 'manual_edit', 'malformed_markers', 'network',
                      'verification', 'invalid_fragment', 'destination', 'transport', 'auth',
                      'remote_data', 'publish_failed', 'processing_failed',
                      'timezone_data_unavailable', 'configuration_failed'}
            if type(value.get('state')) is str and value['state'] in states:
                result['state'] = value['state']
            if type(value.get('error_code')) is str and value['error_code'] in errors:
                result['error_code'] = value['error_code']
            result['last_run_counts'] = {k: value[k] for k in keys if type(value.get(k)) is int}
            if isinstance(value.get('counts'), dict):
                result['counts'] = {k: value['counts'][k] for k in keys
                                    if type(value['counts'].get(k)) is int}
            if type(value.get('pid')) is int:
                result['pid'] = value['pid']
            # Parse dates before exposing them; arbitrary diagnostic text stays local.
            from datetime import datetime
            for key in ('checked_at', 'last_success'):
                if isinstance(value.get(key), str):
                    try:
                        result[key] = datetime.fromisoformat(value[key].replace('Z', '+00:00')).isoformat()
                    except ValueError:
                        pass
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'start', 'stop', 'status', 'uninstall'])
    parser.add_argument('--python')
    parser.add_argument('--gh')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--enable', action='store_true', help='Explicitly enable delivery after installation')
    parser.add_argument('--home', type=Path, default=Path.home())
    args = parser.parse_args(argv)
    try:
        if args.prepare_only and (args.action != 'install' or args.enable):
            raise ValueError('--prepare-only requires install and cannot be combined with --enable')
        if args.enable and args.action != 'install':
            raise ValueError('--enable applies only to install; use start for an existing installation')
        if not args.prepare_only and (sys.platform != 'darwin' or args.home != Path.home()):
            raise ValueError('Live actions require macOS and current user home; use install --prepare-only')
        os.umask(0o077)
        p = paths(args.home)
        domain = 'gui/' + str(os.getuid()) if not args.prepare_only else None
        if args.action == 'install':
            if not args.prepare_only and (loaded(domain) or worker_locked(p['data'])):
                raise ValueError('Summary job is active; run stop before reinstalling')
            python = find_python(args.python)
            result = prepare(p, python, find_gh(args.gh))
            if args.enable:
                result.update(start(p, python, domain))
        elif args.action == 'start':
            result = start(p, find_python(args.python), domain)
        elif args.action == 'status':
            result = status(p, domain)
        else:
            result = stop(p, domain)
            if args.action == 'uninstall':
                regular(p['plist']).unlink(missing_ok=True)
                result.update({'uninstalled': True, 'retained_private_files': str(p['base'])})
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'error': type(error).__name__, 'detail': str(error)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
