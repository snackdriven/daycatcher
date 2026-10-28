"""Headless collector commands. stdout is JSON; routine output contains no bodies."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import signal
import sys
import time

from .store import Store
from .runtime import worker_lock, worker_health


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest='command', required=True)
    for name in ('init', 'scan', 'watch', 'ingest', 'cursor', 'status', 'preview', 'exclude'):
        command = commands.add_parser(name)
        command.add_argument('--config', type=Path)
        command.add_argument('--db', type=Path)
        command.add_argument('--data-dir', type=Path)
        if name == 'ingest':
            command.add_argument('--historical', '--preserve-existing-complete',
                                 dest='preserve_existing_complete', action='store_true')
        if name in ('scan', 'watch'):
            command.add_argument('--codex-root', type=Path, action='append')
            command.add_argument('--claude-root', type=Path, action='append')
            command.add_argument('--since')
            command.add_argument('--max-bytes', type=int)
        if name == 'watch':
            command.add_argument('--interval', type=float)
            command.add_argument('--max-scans', type=int, default=0)
        if name == 'cursor':
            command.add_argument('--source', required=True)
            command.add_argument('--key', required=True)
        if name == 'preview':
            command.add_argument('--source')
            command.add_argument('--limit', type=int, default=20)
            command.add_argument('--text', action='store_true')
        if name == 'exclude':
            command.add_argument('--message-key', required=True)
            command.add_argument('--reason', default='user-excluded')
    return result


def configuration(args):
    config = {}
    if args.config:
        config = json.loads(args.config.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('Configuration must be a JSON object')
    data = args.data_dir or config.get('data_dir')
    db = args.db or config.get('db')
    if db is None and data is not None:
        db = Path(data).expanduser() / 'collector.sqlite3'
    if db is None:
        raise ValueError('Choose a private data directory with --data-dir, --db, or --config')
    db = Path(db).expanduser().resolve()
    directory = Path(data).expanduser().resolve() if data else db.parent
    db.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir(parents=True, exist_ok=True)
    return config, directory, db


def local_options(args, config):
    settings = config.get('local', {})
    if not isinstance(settings, dict):
        raise ValueError('local configuration must be an object')
    options = {}
    for name in ('codex_roots', 'claude_roots'):
        cli_name = 'codex_root' if name == 'codex_roots' else 'claude_root'
        value = getattr(args, cli_name, None)
        value = settings.get(name) if value is None else value
        if value is not None:
            if not isinstance(value, list) or any(not isinstance(x, (str, Path)) for x in value):
                raise ValueError(f'{name} must be a list of paths or null')
            value = [Path(x).expanduser().resolve() for x in value]
        options[name] = value
    options['since'] = args.since or settings.get('since')
    if options['since'] is None:
        raise ValueError('Set a fixed collection horizon with --since or local.since in configuration')
    options['max_bytes'] = args.max_bytes if args.max_bytes is not None else settings.get('max_bytes', 16777216)
    if type(options['max_bytes']) is not int or options['max_bytes'] < 1 or options['max_bytes'] > 1073741824:
        raise ValueError('max_bytes must be between 1 and 1073741824')
    return options


def watch(store, args, config, directory):
    from .local import collect_local
    options = local_options(args, config)
    interval = args.interval if args.interval is not None else config.get('poll_seconds', 15)
    if type(interval) not in (int, float) or not math.isfinite(interval) or interval < 0.01 or interval > 86400:
        raise ValueError('interval must be between 0.01 and 86400 seconds')
    if args.max_scans < 0:
        raise ValueError('max-scans must be zero or positive')
    stop = directory / 'STOP'
    health = directory / 'local-status.json'
    stopping = False
    def request_stop(*_):
        nonlocal stopping
        stopping = True
    prior = {}
    for name in ('SIGINT', 'SIGTERM'):
        sig = getattr(signal, name, None)
        if sig is not None:
            prior[sig] = signal.signal(sig, request_stop)
    scans = 0
    reason = 'stop_file'
    with worker_lock(directory):
        try:
            atomic_json(health, {'state': 'running', 'pid': os.getpid(), 'parent_pid': os.getppid(), 'started_at': utcnow(), 'scans': 0})
            while not stop.exists() and not stopping:
                try:
                    result = collect_local(store, **options)
                except Exception as error:
                    # Source bodies and arbitrary exception messages never enter logs.
                    result = {'status': 'error', 'error_type': type(error).__name__}
                scans += 1
                atomic_json(health, {'state': 'running', 'pid': os.getpid(), 'parent_pid': os.getppid(), 'checked_at': utcnow(), 'scans': scans, 'scan': result})
                if args.max_scans and scans >= args.max_scans:
                    reason = 'max_scans'; break
                deadline = time.monotonic() + interval
                while time.monotonic() < deadline and not stop.exists() and not stopping:
                    time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            if stopping:
                reason = 'signal'
            return {'state': 'stopped', 'reason': reason, 'scans': scans}
        finally:
            atomic_json(health, {'state': 'stopped', 'reason': reason, 'pid': os.getpid(), 'checked_at': utcnow(), 'scans': scans})
            for sig, handler in prior.items():
                signal.signal(sig, handler)


def execute(args):
    config, directory, db = configuration(args)
    with Store(db) as store:
        if args.command == 'init':
            target = directory / 'collector.json'
            if not target.exists():
                atomic_json(target, {'version': 1, 'data_dir': str(directory), 'db': str(db), 'poll_seconds': 15,
                    'local': {'codex_roots': None, 'claude_roots': None,
                        'since': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), 'max_bytes': 16777216}})
            return {'initialized': True, 'db': str(db), 'config': str(target)}
        if args.command == 'ingest':
            raw = sys.stdin.buffer.read(33554433)
            if len(raw) > 33554432:
                raise ValueError('Batch exceeds 32 MiB limit')
            return store.ingest_batch(json.loads(raw), preserve_existing_complete=args.preserve_existing_complete)
        if args.command == 'cursor':
            return store.get_cursor(args.source, args.key)
        if args.command == 'status':
            result = store.status()
            result['workers'] = worker_health(directory)
            return result
        if args.command == 'preview':
            if args.limit < 1 or args.limit > 1000:
                raise ValueError('limit must be between 1 and 1000')
            rows = store.messages(limit=args.limit, source=args.source)
            if args.text:
                return rows
            return [{k: v for k, v in row.items() if k not in {'text', 'native_id', 'conversation_id', 'conversation_ids', 'provenance'}} |
                    {'text_characters': len(row.get('text') or '')} for row in rows]
        if args.command == 'exclude':
            return store.exclude(args.message_key, args.reason)
        if args.command == 'scan':
            from .local import collect_local
            with worker_lock(directory):
                return collect_local(store, **local_options(args, config))
        if args.command == 'watch':
            return watch(store, args, config, directory)
    raise ValueError('Unknown command')


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as error:
        # Validation errors are fixed messages; OS errors can contain paths but no source bodies.
        print(json.dumps({'error': type(error).__name__, 'detail': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
