"""Local deterministic work context and durable delivery receipts. No model calls.

Only enum codes leave this module. Source bodies are read through SQLite mode=ro;
the separate queue stores hashes and revision references, never transcript text.
"""
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


TOPICS = {
    'topic_testing': r'\b(test(?:s|ing|ed)?|qa|verification|verify|regression)\b',
    'topic_troubleshooting': r'\b(debug(?:ging)?|troubleshoot(?:ing)?|bug|failure|broken|error)\b',
    'topic_building': r'\b(build(?:ing)?|implement(?:ing|ation)?|develop(?:ing|ment)?|feature|code|coding)\b',
    'topic_writing': r'\b(writ(?:e|ing|ten)|draft(?:ing)?|documentation|documenting)\b',
    'topic_planning': r'\b(plan(?:s|ning)?|design(?:ing)?|scope|proposal)\b',
    'topic_coordination': r'\b(coordinat(?:e|ing|ion)|meeting|handoff|review(?:ing)?|collaborat(?:e|ing|ion))\b',
}
REACTIONS = {'frustrated', 'pleased', 'tired', 'curious', 'relieved'}
FACT_CODES = frozenset(TOPICS) | frozenset('reaction_' + word for word in REACTIONS)
_MAINTENANCE = re.compile(
    r'work[- _]summar(?:y|ies)|day[- _]crumbs|conversation.?collector|journal.?collector|'
    r'collector[- _]core|summary[_-](?:service|publish|install)|synthetic|probe|'
    r'<(?:system-reminder|task-notification|environment_context)|'
    r'Message Type:|NEW_TASK|Payload:|relay|harness|Remote Control|tina \| orchestrator', re.I)


def journal_day(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp_timezone_required')
    local = parsed.astimezone(ZoneInfo('America/Chicago'))
    day = local.date() - (timedelta(days=1) if local.hour < 3 else timedelta())
    return day.isoformat()


def classify(text, role, complete, provenance):
    """Topics describe discussion; feelings require an entire direct assertion.

    Deliberately abstains on longer prose, quotes, qualifications, questions,
    negation, plans and sarcasm markers. No completed-action classification.
    """
    if not isinstance(text, str) or _MAINTENANCE.search(text):
        return []
    if any(provenance.get(key) for key in ('synthetic', 'processing_generated', 'relay', 'is_meta')):
        return []
    facts = [code for code, pattern in TOPICS.items() if re.search(pattern, text, re.I)]
    human = provenance.get('origin') in {'human', 'native-user-role'}
    if complete and role == 'user' and human:
        match = re.fullmatch(r"\s*(?:I am|I'm|I’m) (frustrated|pleased|tired|curious|relieved)[.!]?\s*", text, re.I)
        if match:
            facts.append('reaction_' + match.group(1).lower())
    return sorted(facts)


def _fingerprint(row):
    value = {key: row[key] for key in ('message_key', 'revision', 'text', 'role',
                                       'source_time', 'complete', 'provenance')}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _eligible(row):
    try:
        provenance = json.loads(row['provenance'])
        if not isinstance(provenance, dict) or provenance.get('unsupported_content'):
            return None
        if provenance.get('timestamp_valid') is not True:
            return None
        # Incomplete context is permitted only for the known origin-only case.
        if not row['complete'] and provenance.get('origin') != 'uncertain':
            return None
        day = journal_day(row['source_time'])
        facts = classify(row['text'], row['role'], bool(row['complete']), provenance)
        return day, facts
    except (ValueError, TypeError, AttributeError, KeyError):
        return None


class SummaryQueue:
    def __init__(self, source_db, queue_db, *, eligibility=None, namespace='work'):
        if namespace not in {'work', 'personal'}:
            raise ValueError('invalid_queue_namespace')
        self.eligibility = eligibility or _eligible
        source_path, queue_path = Path(source_db).resolve(), Path(queue_db).resolve()
        if source_path == queue_path:
            raise ValueError('separate_queue_required')
        # Fail at startup if this platform has no IANA database; never disguise a
        # missing timezone runtime as an absence of eligible material.
        ZoneInfo('America/Chicago')
        self.source = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True, timeout=10)
        self.source.row_factory = sqlite3.Row
        self.source.execute('PRAGMA query_only=ON')
        self.connection = sqlite3.connect(queue_path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA busy_timeout=10000')
        existing = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name IN ('processed','fragments','support','meta')").fetchone()
        self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS processed (
                message_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                PRIMARY KEY(message_key,fingerprint));
            CREATE TABLE IF NOT EXISTS fragments (
                fragment_id TEXT PRIMARY KEY, day TEXT NOT NULL, facts TEXT NOT NULL,
                coverage TEXT NOT NULL, refs TEXT NOT NULL, state TEXT NOT NULL,
                receipt TEXT, last_error TEXT);
            CREATE TABLE IF NOT EXISTS support (
                day TEXT NOT NULL, code TEXT NOT NULL, message_key TEXT NOT NULL,
                revision INTEGER NOT NULL, fingerprint TEXT NOT NULL,
                PRIMARY KEY(day,code,message_key,fingerprint));
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        try:
            # Bind once under the SQLite write lock. Older queues have no
            # namespace marker and belong to work, including an empty old queue.
            self.connection.execute('BEGIN IMMEDIATE')
            profile = self.connection.execute("SELECT value FROM meta WHERE key='namespace'").fetchone()
            current = profile['value'] if profile else ('work' if existing else namespace)
            if current != namespace:
                raise ValueError('queue_profile_mismatch')
            self.connection.execute("INSERT OR IGNORE INTO meta VALUES('namespace',?)", (namespace,))
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            self.close()
            raise

    def _eligible(self, row):
        return self.eligibility(row)

    def close(self):
        self.source.close()
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    @staticmethod
    def _limit(limit):
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('invalid_limit')

    def _valid_ref(self, ref):
        row = self.source.execute('SELECT * FROM messages WHERE message_key=?', (ref['key'],)).fetchone()
        excluded = self.source.execute('SELECT 1 FROM exclusions WHERE message_key=?', (ref['key'],)).fetchone()
        return row is not None and not excluded and _fingerprint(row) == ref['hash'] and self._eligible(row) is not None

    def _validate(self):
        # Never mutate a prepared payload. Invalidated pending IDs retire forever.
        rows = self.connection.execute("SELECT * FROM fragments WHERE state IN ('ready','publishing','delivered')").fetchall()
        for row in rows:
            refs = json.loads(row['refs'])
            if all(self._valid_ref(ref) for ref in refs):
                continue
            state = 'invalidated' if row['state'] == 'ready' else 'review_required'
            self.connection.execute('UPDATE fragments SET state=?,last_error=? WHERE fragment_id=?',
                                    (state, 'source_changed', row['fragment_id']))
        # Evidence metadata is local; remove stale support before it can be reused.
        for row in self.connection.execute('SELECT * FROM support').fetchall():
            if not self._valid_ref({'key': row['message_key'], 'hash': row['fingerprint']}):
                self.connection.execute('DELETE FROM support WHERE day=? AND code=? AND message_key=? AND fingerprint=?',
                                        (row['day'], row['code'], row['message_key'], row['fingerprint']))

    def prepare(self, limit=500):
        self._limit(limit)
        counts = {'scanned': 0, 'processed': 0, 'skipped': 0, 'prepared': 0}
        with self.connection:
            self._validate()
            position = self.connection.execute("SELECT value FROM meta WHERE key='position'").fetchone()
            after = position['value'] if position else ''
            rows = self.source.execute('SELECT * FROM messages WHERE message_key>? ORDER BY message_key LIMIT ?',
                                       (after, limit)).fetchall()
            if not rows and after:
                rows = self.source.execute('SELECT * FROM messages ORDER BY message_key LIMIT ?', (limit,)).fetchall()
            for row in rows:
                counts['scanned'] += 1
                fingerprint = _fingerprint(row)
                old = self.connection.execute('SELECT 1 FROM processed WHERE message_key=? AND fingerprint=?',
                                              (row['message_key'], fingerprint)).fetchone()
                if old:
                    continue
                self.connection.execute('INSERT OR IGNORE INTO processed VALUES(?,?)', (row['message_key'], fingerprint))
                counts['processed'] += 1
                eligible = self._eligible(row)
                if not eligible:
                    counts['skipped'] += 1
                    continue
                day, facts = eligible
                for code in facts:
                    self.connection.execute('INSERT OR IGNORE INTO support VALUES(?,?,?,?,?)',
                                            (day, code, row['message_key'], row['revision'], fingerprint))
            if rows:
                self.connection.execute("INSERT OR REPLACE INTO meta VALUES('position',?)", (rows[-1]['message_key'],))
            for dayrow in self.connection.execute('SELECT DISTINCT day FROM support ORDER BY day').fetchall():
                day = dayrow['day']
                if self.connection.execute("SELECT 1 FROM fragments WHERE day=? AND state='review_required'", (day,)).fetchone():
                    continue
                assigned = set()
                for existing in self.connection.execute("SELECT facts FROM fragments WHERE day=? AND state IN ('ready','publishing','delivered')", (day,)):
                    assigned.update(json.loads(existing['facts']))
                support = self.connection.execute('SELECT * FROM support WHERE day=? ORDER BY code,message_key', (day,)).fetchall()
                fresh = sorted({item['code'] for item in support} - assigned)
                if not fresh:
                    continue
                refs = { (item['message_key'], item['revision'], item['fingerprint']) for item in support if item['code'] in fresh }
                refs = [{'key': key, 'revision': revision, 'hash': fingerprint} for key, revision, fingerprint in sorted(refs)]
                for ref in refs:
                    timing = self.source.execute('SELECT source_time,observed_at FROM messages WHERE message_key=?',
                                                 (ref['key'],)).fetchone()
                    if timing is not None:
                        ref.update(source_time=timing['source_time'], observed_at=timing['observed_at'])
                # A source deletion/revision race retires this payload on ready();
                # do not create a payload from support that already disappeared.
                if not all(self._valid_ref(ref) for ref in refs):
                    continue
                # Partial is intentional: available file cursors do not prove whole-device coverage.
                self.connection.execute('INSERT INTO fragments VALUES(?,?,?,?,?,?,?,?)',
                                        (uuid.uuid4().hex, day, json.dumps(fresh), 'partial', json.dumps(refs), 'ready', None, None))
                counts['prepared'] += 1
        return counts

    def ready(self, limit=20):
        self._limit(limit)
        with self.connection:
            self._validate()
            return [dict(fragment_id=row['fragment_id'], day=row['day'], facts=json.loads(row['facts']),
                         coverage=row['coverage']) for row in self.connection.execute(
                             """SELECT * FROM fragments AS f WHERE state IN ('ready','publishing')
                             AND NOT EXISTS (SELECT 1 FROM fragments AS blocked
                                             WHERE blocked.day=f.day AND blocked.state='review_required')
                             ORDER BY day,fragment_id LIMIT ?""", (limit,))]

    def begin_publish(self, fragment_id):
        """Durably mark the ambiguous remote-success window before networking.

        Retries keep the same ID for the publisher's GET reconciliation. If
        evidence changes after an attempt, only review may resolve that ID.
        """
        with self.connection:
            self._validate()
            row = self.connection.execute('SELECT state,day FROM fragments WHERE fragment_id=?',
                                          (fragment_id,)).fetchone()
            if not row or row['state'] not in {'ready', 'publishing'}:
                return False
            if self.connection.execute("SELECT 1 FROM fragments WHERE day=? AND state='review_required'",
                                       (row['day'],)).fetchone():
                return False
            self.connection.execute("UPDATE fragments SET state='publishing' WHERE fragment_id=?", (fragment_id,))
        return True

    def delivered(self, fragment_id, receipt):
        if not isinstance(receipt, dict) or receipt.get('state') not in {'published', 'reconciled', 'delivered'}:
            raise ValueError('invalid_delivery_receipt')
        # Receipt is operational metadata; do not persist arbitrary transport responses.
        safe = {key: receipt[key] for key in ('state', 'commit', 'commit_sha', 'sha', 'blob_sha')
                if key in receipt and isinstance(receipt[key], str) and len(receipt[key]) <= 200}
        with self.connection:
            self._validate()
            row = self.connection.execute('SELECT state FROM fragments WHERE fragment_id=?', (fragment_id,)).fetchone()
            if not row:
                raise ValueError('fragment_not_ready')
            if row['state'] in {'invalidated', 'review_required'}:
                # Publishing may have succeeded while source evidence changed.
                # Retain the remote receipt, but require a deliberate correction.
                self.connection.execute("UPDATE fragments SET state='review_required',receipt=? WHERE fragment_id=?",
                                        (json.dumps(safe), fragment_id))
                return {'state': 'review_required'}
            self.connection.execute("UPDATE fragments SET state='delivered',receipt=?,last_error=NULL WHERE fragment_id=?",
                                    (json.dumps(safe), fragment_id))
        return {'state': 'delivered'}

    def failed(self, fragment_id, code):
        if not isinstance(code, str) or not re.fullmatch('[a-z_]{1,64}', code):
            raise ValueError('invalid_failure_code')
        with self.connection:
            result = self.connection.execute("UPDATE fragments SET last_error=? WHERE fragment_id=? AND state IN ('ready','publishing')", (code, fragment_id))
            if result.rowcount != 1:
                raise ValueError('fragment_not_ready')

    def status(self):
        counts = dict.fromkeys(('ready', 'publishing', 'delivered', 'invalidated', 'review_required'), 0)
        for row in self.connection.execute('SELECT state,COUNT(*) AS n FROM fragments GROUP BY state'):
            counts[row['state']] = row['n']
        counts['processed_revisions'] = self.connection.execute('SELECT COUNT(*) FROM processed').fetchone()[0]
        return {'counts': counts, 'coverage': 'partial', 'model_calls': 0}
