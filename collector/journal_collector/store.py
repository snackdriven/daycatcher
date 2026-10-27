"""Transactional, local-only canonical message storage (Python standard library)."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _identifier(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 1024 or '\x00' in value:
        raise ValueError(f'{name} must be a nonempty string of at most 1024 characters')
    return value


def _timestamp(value, name, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError(f'{name} must be an ISO timestamp with timezone')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if 'T' not in value or parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
    except ValueError as exc:
        raise ValueError(f'{name} must be an ISO timestamp with timezone') from exc


class Store:
    def __init__(self, db_path):
        self.connection = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.execute('PRAGMA busy_timeout=30000')
        self.connection.execute('PRAGMA secure_delete=ON')
        self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS messages (
                message_key TEXT PRIMARY KEY, source TEXT NOT NULL, native_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT,
                source_time TEXT, observed_at TEXT NOT NULL, complete INTEGER NOT NULL,
                revision INTEGER NOT NULL, provenance TEXT NOT NULL,
                UNIQUE(source,native_id));
            CREATE TABLE IF NOT EXISTS associations (
                message_key TEXT NOT NULL REFERENCES messages(message_key),
                conversation_id TEXT NOT NULL, PRIMARY KEY(message_key,conversation_id));
            CREATE TABLE IF NOT EXISTS revisions (
                message_key TEXT NOT NULL REFERENCES messages(message_key), revision INTEGER NOT NULL,
                text TEXT NOT NULL, role TEXT NOT NULL, source_time TEXT, observed_at TEXT NOT NULL,
                provenance TEXT NOT NULL, PRIMARY KEY(message_key,revision));
            CREATE TABLE IF NOT EXISTS jobs (
                message_key TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(message_key,revision),
                FOREIGN KEY(message_key,revision) REFERENCES revisions(message_key,revision));
            CREATE TABLE IF NOT EXISTS exclusions (
                message_key TEXT PRIMARY KEY, reason TEXT NOT NULL, excluded_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cursors (
                source TEXT NOT NULL, cursor_key TEXT NOT NULL, cursor TEXT,
                coverage TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(source,cursor_key));
            CREATE TABLE IF NOT EXISTS batches (
                batch_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, result TEXT NOT NULL);
        ''')

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def get_cursor(self, source, cursor_key):
        row = self.connection.execute('SELECT cursor FROM cursors WHERE source=? AND cursor_key=?',
                                      (source, cursor_key)).fetchone()
        return json.loads(row['cursor']) if row and row['cursor'] is not None else None

    def ingest_batch(self, batch):
        if not isinstance(batch, dict):
            raise ValueError('batch must be an object')
        batch_id = _identifier(batch.get('batch_id'), 'batch_id')
        source = _identifier(batch.get('source'), 'source')
        cursor_key = _identifier(batch.get('cursor_key'), 'cursor_key')
        coverage = batch.get('coverage')
        if not isinstance(coverage, dict) or coverage.get('status') not in {
                'complete', 'partial', 'error', 'authentication_required'}:
            raise ValueError('invalid coverage status')
        successful = coverage['status'] in {'complete', 'partial'}
        items = batch.get('messages')
        if not isinstance(items, list):
            raise ValueError('messages must be a list')
        if not successful and items:
            raise ValueError('error/authentication batches cannot include messages')
        if successful and not isinstance(batch.get('cursor'), dict):
            raise ValueError('successful batches require an object cursor')
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('message must be an object')
            _identifier(item.get('native_id'), 'native_id')
            _identifier(item.get('conversation_id'), 'conversation_id')
            if item.get('role') not in {'user', 'assistant'}:
                raise ValueError('invalid role')
            if type(item.get('complete')) is not bool:
                raise ValueError('complete must be boolean')
            if item.get('complete') and not isinstance(item.get('text'), str):
                raise ValueError('complete message requires text')
            if item.get('text') is not None and not isinstance(item['text'], str):
                raise ValueError('text must be string or null')
            if not isinstance(item.get('provenance', {}), dict):
                raise ValueError('provenance must be an object')
            _timestamp(item.get('source_time'), 'source_time', nullable=True)
            _timestamp(item.get('observed_at'), 'observed_at')
        payload_hash = hashlib.sha256(_json(batch).encode('utf-8')).hexdigest()
        result = dict.fromkeys(('new', 'revised', 'unchanged', 'incomplete', 'excluded'), 0)
        result['batch_id'] = batch_id
        now = datetime.now(timezone.utc).isoformat()
        conn = self.connection
        conn.execute('BEGIN IMMEDIATE')
        try:
            previous = conn.execute('SELECT * FROM batches WHERE batch_id=?', (batch_id,)).fetchone()
            if previous:
                if previous['payload_hash'] != payload_hash:
                    raise ValueError('batch_id already used with different payload')
                conn.execute('COMMIT')
                return json.loads(previous['result'])
            for item in items:
                key = hashlib.sha256(_json([source, item['native_id']]).encode('utf-8')).hexdigest()
                if conn.execute('SELECT 1 FROM exclusions WHERE message_key=?', (key,)).fetchone():
                    result['excluded'] += 1
                    continue
                old = conn.execute('SELECT * FROM messages WHERE message_key=?', (key,)).fetchone()
                complete = item['complete']
                changed = complete and (not old or not old['complete'] or
                                            old['text'] != item['text'] or old['role'] != item['role'])
                revision = ((old['revision'] if old else 0) + 1) if changed else (old['revision'] if old else 0)
                provenance = _json(item.get('provenance', {}))
                if not old or complete or not old['complete']:
                    conn.execute('''INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(message_key) DO UPDATE SET
                        conversation_id=excluded.conversation_id, role=excluded.role, text=excluded.text,
                        source_time=excluded.source_time, observed_at=excluded.observed_at,
                        complete=excluded.complete, revision=excluded.revision, provenance=excluded.provenance''',
                                 (key, source, item['native_id'], item['conversation_id'], item['role'],
                                  item.get('text'), item.get('source_time'), item['observed_at'],
                                  int(complete), revision, provenance))
                conn.execute('INSERT OR IGNORE INTO associations VALUES(?,?)', (key, item['conversation_id']))
                if changed:
                    conn.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?)',
                                 (key, revision, item['text'], item['role'], item.get('source_time'), item['observed_at'], provenance))
                    conn.execute('INSERT INTO jobs VALUES(?,?,?)', (key, revision, now))
                category = 'incomplete' if not complete else 'new' if not old else 'revised' if changed else 'unchanged'
                result[category] += 1
            existing = self.get_cursor(source, cursor_key)
            cursor = _json(batch['cursor']) if successful else (_json(existing) if existing is not None else None)
            conn.execute('''INSERT INTO cursors VALUES(?,?,?,?,?) ON CONFLICT(source,cursor_key)
                DO UPDATE SET cursor=excluded.cursor,coverage=excluded.coverage,updated_at=excluded.updated_at''',
                         (source, cursor_key, cursor, _json(coverage), now))
            conn.execute('INSERT INTO batches VALUES(?,?,?)', (batch_id, payload_hash, _json(result)))
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise
        return result

    def status(self):
        counts = {name: self.connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                  for name, table in [('messages', 'messages'), ('revisions', 'revisions'),
                                      ('pending_jobs', 'jobs'), ('exclusions', 'exclusions'), ('batches', 'batches')]}
        sources = [dict(row) for row in self.connection.execute(
            'SELECT source,cursor_key,coverage,updated_at FROM cursors ORDER BY source,cursor_key')]
        for row in sources:
            row['coverage'] = json.loads(row['coverage'])
        return {'counts': counts, 'sources': sources}

    def messages(self, limit=50, source=None):
        self._limit(limit)
        sql = 'SELECT * FROM messages'
        args = []
        if source is not None:
            sql += ' WHERE source=?'
            args.append(source)
        sql += ' ORDER BY observed_at DESC,message_key LIMIT ?'
        rows = [dict(row) for row in self.connection.execute(sql, [*args, limit])]
        for row in rows:
            row['complete'] = bool(row['complete'])
            row['provenance'] = json.loads(row['provenance'])
            row['conversation_ids'] = [a[0] for a in self.connection.execute(
                'SELECT conversation_id FROM associations WHERE message_key=? ORDER BY conversation_id',
                (row['message_key'],))]
        return rows

    @staticmethod
    def _limit(limit):
        if type(limit) is not int or not 0 <= limit <= 100000:
            raise ValueError('limit must be an integer between 0 and 100000')

    def pending_jobs(self, limit=50):
        self._limit(limit)
        return [dict(row) for row in self.connection.execute('''SELECT jobs.*,messages.source FROM jobs
            JOIN messages USING(message_key) ORDER BY created_at,message_key,revision LIMIT ?''', (limit,))]

    def exclude(self, message_key, reason='user-excluded'):
        _identifier(message_key, 'message_key')
        _identifier(reason, 'reason')
        conn = self.connection
        conn.execute('BEGIN IMMEDIATE')
        try:
            conn.execute('INSERT OR IGNORE INTO exclusions VALUES(?,?,?)',
                         (message_key, reason, datetime.now(timezone.utc).isoformat()))
            for table in ('jobs', 'revisions', 'associations', 'messages'):
                conn.execute('DELETE FROM ' + table + ' WHERE message_key=?', (message_key,))
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise
        return {'message_key': message_key, 'excluded': True}
