"""Idempotent hidden sign-in entrypoint; invoke with pythonw and --config PATH."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

import manage
from journal_collector.cli import atomic_json
from journal_collector.runtime import worker_health, worker_lock


@contextmanager
def startup_lock(data):
    # Separate from the worker lock, which the running collector must retain.
    deadline = time.monotonic() + 30
    while True:
        lock = worker_lock(data / '.startup')
        try:
            lock.__enter__()
            break
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise RuntimeError('startup lock timeout') from None
            time.sleep(0.1)
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def record(data, event, ok):
    # Fixed vocabulary only. Never include exception text, captured output, config,
    # source names, transcript bodies, or credentials in startup diagnostics.
    value = {'time': datetime.now(timezone.utc).isoformat(), 'event': event,
             'ok': ok, 'launcher_pid': os.getpid()}
    atomic_json(data / 'startup-status.json', value)
    with (data / 'startup.log').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value) + '\n')
    return 0 if ok else 1


def complete(health, bridge_enabled):
    return (health.get('local', {}).get('state') == 'running'
            and (not bridge_enabled or bool(health.get('claude', {}).get('active'))))


def manager(action, config_path):
    # pythonw supplies None for stdout/stderr. Do not depend on either stream or
    # persist manager errors, which might someday include private input values.
    with open(os.devnull, 'w', encoding='utf-8') as sink:
        with redirect_stdout(sink), redirect_stderr(sink):
            return manage.main([action, '--config', str(config_path)])


def main(argv=None):
    data = None
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--config', type=Path, required=True)
        with open(os.devnull, 'w', encoding='utf-8') as sink:
            with redirect_stdout(sink), redirect_stderr(sink):
                args = parser.parse_args(argv)
        config_path = args.config.expanduser().resolve()
        # Invalid configuration diagnostics live beside the supplied config.
        data = config_path.parent
        config = json.loads(config_path.read_text(encoding='utf-8'))
        for key in ('data_dir', 'db'):
            if not isinstance(config.get(key), str) or not Path(config[key]).is_absolute():
                raise ValueError('absolute paths required')
        bridge = config.get('claude', {})
        if not isinstance(bridge, dict):
            raise ValueError('invalid bridge config')
        bridge_enabled = bool(bridge.get('enabled'))
        data = Path(config['data_dir']).resolve()
        data.mkdir(parents=True, exist_ok=True)
        with startup_lock(data):
            health = worker_health(data)
            if complete(health, bridge_enabled):
                return record(data, 'already_running', True)
            # Even missing/stale health can accompany a live worker. The manager
            # requests graceful shutdown through this configured directory only;
            # it never kills a PID recovered from stale metadata.
            partial = (health.get('local', {}).get('state') == 'running'
                       or bool(health.get('claude', {}).get('active'))
                       or manage.local_is_locked(data) or manage.bridge_is_locked(data))
            stop = data / 'STOP'
            if stop.is_symlink() or (stop.exists() and stop.resolve().parent != data):
                raise ValueError('unsafe stop control path')
            if partial and manager('stop', config_path) != 0:
                return record(data, 'recovery_failed', False)
            manager('start', config_path)
            # Another manual starter can win the worker lock race. Recheck the
            # verified health instead of treating an already-running error as fatal.
            if complete(worker_health(data), bridge_enabled):
                return record(data, 'recovered' if partial else 'started', True)
            return record(data, 'start_failed', False)
    except SystemExit:
        return 1
    except Exception:
        if data is not None:
            try:
                record(data, 'startup_error', False)
            except OSError:
                pass  # Unwritable diagnostic paths still produce a failure exit.
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
