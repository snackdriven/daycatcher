"""Start/status/stop background collectors without opening a desktop window."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from journal_collector.cli import atomic_json, worker_lock
from journal_collector.runtime import local_is_locked, process_alive

ROOT = Path(__file__).resolve().parent


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def launch(command, log_path, *, electron=False):
    env = dict(os.environ)
    env['PYTHONPATH'] = str(ROOT)
    if electron:
        env.pop('ELECTRON_RUN_AS_NODE', None)
    with log_path.open('ab') as log:
        return subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            start_new_session=os.name != 'nt')


def await_health(process, path, *, bridge=False):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        health = read_json(path)
        # Windows virtual environments launch Python through a redirector.
        # Its child writes health under its own PID while Popen owns the parent.
        matches_process = health.get('pid') == process.pid or (
            not bridge and health.get('parent_pid') == process.pid)
        is_running = health.get('active') if bridge else (
            health.get('state') == 'running' and local_is_locked(path.parent))
        if process.poll() is None and matches_process and is_running:
            return health
        if process.poll() is not None:
            raise RuntimeError('Worker exited during startup; inspect its local log')
        time.sleep(0.1)
    raise RuntimeError('Worker startup not confirmed; inspect its status and log')


def bridge_is_locked(data):
    path = data / 'claude-bridge.lock'
    if not path.exists():
        return False
    try:
        return process_alive(int(path.read_text()))
    except (OSError, ValueError):
        raise RuntimeError('Bridge lock is unreadable; inspect it before restarting')


def stop_workers(data):
    stop = data / 'STOP'
    # Only this exact control file in the configured runtime directory is written.
    stop.write_text('Requested through collector manager\n', encoding='utf-8')
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        bridge_lock = bridge_is_locked(data)
        if not local_is_locked(data) and not bridge_lock:
            return {'stopped': True, 'control_file': str(stop)}
        time.sleep(0.1)
    return {'stopped': False, 'stop_requested': True, 'detail': 'A request may still be finishing; inspect status before restarting'}


def cleanup_started(processes, data):
    if not processes:
        return
    local = processes[0]
    # Never stop another starter's worker after losing a startup race.
    health = read_json(data / 'local-status.json')
    if local.poll() is None and (health.get('pid') == local.pid or health.get('parent_pid') == local.pid):
        (data / 'STOP').write_text('Startup failed; stopping owned workers\n', encoding='utf-8')
    for process in reversed(processes):
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=3)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'status', 'stop'))
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args(argv)
    launched = []
    data = None
    try:
        config_path = args.config.expanduser().resolve()
        config = json.loads(config_path.read_text(encoding='utf-8'))
        if not isinstance(config.get('data_dir'), str) or not Path(config['data_dir']).is_absolute() or not isinstance(config.get('db'), str) or not Path(config['db']).is_absolute():
            raise ValueError('Configured data_dir and db must be absolute paths')
        data = Path(config['data_dir']).expanduser().resolve()
        data.mkdir(parents=True, exist_ok=True)
        if args.action == 'status':
            completed = subprocess.run([sys.executable, '-m', 'journal_collector', 'status', '--config', str(config_path)],
                cwd=ROOT, capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if completed.returncode:
                raise RuntimeError('Cannot read collector status')
            print(completed.stdout.strip())
            return 0
        if args.action == 'stop':
            result = stop_workers(data)
            print(json.dumps(result))
            return 0 if result['stopped'] else 1
        if local_is_locked(data) or bridge_is_locked(data):
            raise RuntimeError('A collector is already running; use status or stop first')
        bridge = config.get('claude', {})
        if not isinstance(bridge, dict):
            raise ValueError('Claude configuration must be an object')
        if bridge.get('enabled'):
            for name in ('electron', 'config'):
                if not isinstance(bridge.get(name), str) or not Path(bridge[name]).is_absolute() or not Path(bridge[name]).is_file():
                    raise ValueError('Claude electron/config must be existing absolute file paths')
        stop = data / 'STOP'
        if stop.exists():
            if stop.is_symlink() or stop.resolve().parent != data:
                raise ValueError('Unsafe stop control-file path')
            stop.unlink()
        local = launch([sys.executable, '-m', 'journal_collector', 'watch', '--config', str(config_path)], data / 'local-worker.log')
        launched.append(local)
        await_health(local, data / 'local-status.json')
        result = {'manager_pid': os.getpid(), 'local': {'running': True, 'pid': local.pid}, 'claude': {'enabled': False}}
        if bridge.get('enabled'):
            process = launch([bridge['electron'], str(ROOT / 'claude_bridge' / 'main.cjs'), '--config', bridge['config']],
                data / 'claude-worker.log', electron=True)
            launched.append(process)
            health = await_health(process, data / 'claude-status.json', bridge=True)
            result['claude'] = {'enabled': True, 'running': True, 'pid': process.pid, 'hidden': health.get('hidden')}
        atomic_json(data / 'processes.json', result)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
        if data is not None:
            cleanup_started(launched, data)
        print(json.dumps({'error': type(error).__name__, 'detail': str(error)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
