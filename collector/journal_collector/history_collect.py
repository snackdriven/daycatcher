"""Bounded, independently stoppable historical local ingestion.

Queue paths are private runtime data. Public results contain counts only. A queue
checkpoint follows the canonical transaction: a crash between them safely retries
the canonical history cursor without duplicating revisions or jobs.
"""
from collections import Counter
from contextlib import closing
import argparse
import json
from pathlib import Path
import sqlite3
import time

from .cli import atomic_json, utcnow
from .local import _discover, collect_local
from .runtime import worker_lock
from .store import Store

MAX_BYTES = 16 * 1024 * 1024
CURSOR_NAMESPACE = 'history-local-v1'


def load_config(path):
    path = Path(path).expanduser()
    if not path.is_absolute():
        raise ValueError('history requires an absolute configuration path')
    config = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(config, dict) or not isinstance(config.get('local', {}), dict):
        raise ValueError('configuration and local settings must be objects')
    data = config.get('data_dir')
    db = config.get('db')
    if not data and not db:
        raise ValueError('configuration requires data_dir or db')
    db = Path(db).expanduser() if db else Path(data).expanduser()/'collector.sqlite3'
    data = Path(data).expanduser() if data else db.parent
    if not db.is_absolute() or not data.is_absolute():
        raise ValueError('runtime paths must be absolute')
    local = config.get('local', {})
    roots = {}
    defaults = {'codex_roots': [Path.home()/'.codex'/'sessions', Path.home()/'.codex'/'archived_sessions'],
                'claude_roots': [Path.home()/'.claude'/'projects']}
    for key in defaults:
        value = local.get(key)
        value = defaults[key] if value is None else value
        if not isinstance(value, list) or any(not isinstance(p, (str, Path)) for p in value):
            raise ValueError('local roots must be lists of paths')
        roots[key] = [Path(p).expanduser().resolve() for p in value]
    return {**config, 'data_dir': data.resolve(), 'db': db.resolve(),
            'history_dir': data.resolve()/'history', 'local': roots}


def run_once(config, *, max_files=32):
    """Run one <=16MiB pass; accept load_config's dict or an absolute config path."""
    if not isinstance(config, dict):
        config = load_config(config)
    if type(max_files) is not int or not 1 <= max_files <= 1024:
        raise ValueError('max_files must be between 1 and 1024')
    directory = Path(config['history_dir'])
    directory.mkdir(parents=True, exist_ok=True)
    if (directory/'STOP').exists():
        return {'status': 'stopped', 'reason': 'stop_file', 'files_scanned': 0}
    with worker_lock(directory):
        return _run_locked(config, max_files)


def _run_locked(config, max_files):
    directory = Path(config['history_dir'])
    Path(config['db']).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory/'queue.sqlite3')) as queue, Store(config['db']) as store:
        queue.row_factory = sqlite3.Row
        queue.execute('''CREATE TABLE IF NOT EXISTS files (
            source TEXT, path TEXT, size INTEGER, fingerprint TEXT, state TEXT,
            attempts INTEGER DEFAULT 0, PRIMARY KEY(source,path))''')
        errors, missing, discovered = 0, 0, 0
        # A completed traversal proves only this local inventory, never an account.
        with queue:
            prior_states = {(row['source'], row['path']): row['state']
                            for row in queue.execute('SELECT source,path,state FROM files')}
            queue.execute("UPDATE files SET state='missing' WHERE state!='missing'")
            for source, key in [('codex-local', 'codex_roots'), ('claude-code-local', 'claude_roots')]:
                roots = config['local'][key]
                missing += sum(not Path(root).exists() for root in roots)
                paths, failures = _discover(roots, source)
                errors += len(failures)
                for path in paths:
                    try:
                        stat = path.stat()
                    except OSError:
                        errors += 1
                        continue
                    discovered += 1
                    fingerprint = json.dumps([stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns])
                    old = queue.execute('SELECT * FROM files WHERE source=? AND path=?',
                                        (source, str(path.resolve()))).fetchone()
                    # Preserve a settled checkpoint only if this exact inventory is unchanged.
                    state = old['state'] if old and old['fingerprint'] == fingerprint else 'pending'
                    if state == 'missing':
                        # Discovery marking must not discard the previous persisted outcome.
                        state = prior_states.get((source, str(path.resolve())), 'pending')
                        if state == 'missing':
                            state = 'pending'
                    queue.execute('''INSERT INTO files(source,path,size,fingerprint,state) VALUES(?,?,?,?,?)
                        ON CONFLICT(source,path) DO UPDATE SET size=excluded.size,
                        fingerprint=excluded.fingerprint,state=excluded.state''',
                        (source, str(path.resolve()), stat.st_size, fingerprint, state))
        remaining, scanned = MAX_BYTES, 0
        counts = Counter()
        rows = queue.execute("SELECT * FROM files WHERE state IN ('pending','error') ORDER BY attempts,size,path LIMIT ?",
                             (max_files,)).fetchall()
        for row in rows:
            if remaining <= 0 or (directory/'STOP').exists():
                break
            options = {'codex_roots': [], 'claude_roots': []}
            options['codex_roots' if row['source'] == 'codex-local' else 'claude_roots'] = [row['path']]
            scan = collect_local(store, **options, since=None, max_bytes=remaining,
                                 cursor_namespace=CURSOR_NAMESPACE, preserve_existing_complete=True)
            scanned += 1
            remaining -= scan['bytes_read']
            counts.update(scan['counts'])
            file = scan['files'][0] if scan['files'] else None
            if scan['errors'] or file is None:
                state = 'error'
            elif file['read_offset'] == file['observed_size']:
                state = 'eof' if file['status'] == 'complete' else 'gap'
            elif file['read_offset'] - file['offset'] >= MAX_BYTES:
                # offset advances through complete lines; this difference is only
                # the pending indivisible line, never the full file/chunk size.
                state = 'gap'
            else:
                state = 'pending'
            with queue:
                queue.execute('UPDATE files SET state=?,attempts=attempts+1 WHERE source=? AND path=?',
                              (state, row['source'], row['path']))
        states = {name: 0 for name in ('pending', 'eof', 'gap', 'error', 'missing')}
        states.update(dict(queue.execute('SELECT state,COUNT(*) FROM files GROUP BY state').fetchall()))
        result = {'status': 'stopped' if (directory/'STOP').exists() else
                  'partial' if states['pending'] else 'gaps' if errors or missing or
                  states['gap'] or states['error'] or states['missing'] else 'complete',
                  'files_scanned': scanned, 'bytes_read': MAX_BYTES-remaining, 'counts': dict(counts),
                  'queue': states, 'discovery': {'files': discovered, 'errors': errors,
                  'missing_roots': missing, 'scope': 'local_inventory', 'end_proven': errors == 0 and missing == 0}}
        atomic_json(directory/'status.json', {**result, 'checked_at': utcnow()})
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--once', action='store_true')
    mode.add_argument('--watch', action='store_true')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.once:
            print(json.dumps(run_once(config)), flush=True)
            return 0
        with worker_lock(config['history_dir']):
            while not (config['history_dir']/'STOP').exists():
                print(json.dumps(_run_locked(config, 32)), flush=True)
                for _ in range(150):
                    if (config['history_dir']/'STOP').exists():
                        return 0
                    time.sleep(0.2)
            return 0
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
        print(json.dumps({'status': 'error', 'error_type': type(error).__name__}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
