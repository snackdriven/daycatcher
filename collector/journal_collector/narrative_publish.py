"""Private narrative delivery; support membership is mechanical, not semantic review.

Keep this receipt database: it owns exact spans and remembers deleted material.
The caller must independently review claim strength and permitted outbound content.
No models or raw source readers are invoked. All unowned entry bytes are preserved.
"""
from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid
from datetime import date

from .summary_publish import Publisher, PublishError, REPOSITORY, BRANCH


class NarrativeError(RuntimeError):
    pass


class StopAwareTransport:
    """Guard each remote call; accepted writes may finish readback acknowledgement."""
    def __init__(self, transport, stop_path=None):
        self.transport = transport
        self.stop_path = Path(stop_path) if stop_path is not None else None
        self._acknowledging = False

    def check(self):
        if self.stop_path is not None and (self.stop_path.exists() or self.stop_path.is_symlink()):
            raise NarrativeError('stopped')

    @contextmanager
    def acknowledge(self):
        previous = self._acknowledging
        self._acknowledging = True
        try:
            yield
        finally:
            self._acknowledging = previous

    def repo(self):
        if not self._acknowledging:
            self.check()
        return self.transport.repo()

    def read(self, path):
        if not self._acknowledging:
            self.check()
        return self.transport.read(path)

    def write(self, path, content, sha):
        self.check()
        return self.transport.write(path, content, sha)


_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_MARKER = re.compile(rb'<!-- journal-narrative:([0-9a-f]{32}):(begin|end) -->')
_BOILERPLATE = re.compile(r'(?:work|personal) context from conversations|topics included|partial coverage|I explicitly described feeling', re.I)
_MAX = 1024 * 1024


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode('utf-8')).hexdigest()


def validate_entry(entry, manifest):
    """Validate paragraph/support shape; never assert factual entailment from IDs."""
    if (type(entry) is not dict or set(entry) != {'version', 'day', 'paragraphs'}
            or type(entry['version']) is not int or entry['version'] != 1
            or type(manifest) is not dict or set(manifest) != {'version', 'day', 'evidence'}
            or type(manifest['version']) is not int or manifest['version'] != 1):
        raise NarrativeError('invalid_schema')
    day = entry['day']
    try:
        if type(day) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise ValueError()
        date.fromisoformat(day)
    except ValueError:
        raise NarrativeError('invalid_day') from None
    if manifest['day'] != day:
        raise NarrativeError('day_mismatch')
    if type(manifest['evidence']) is not list or len(manifest['evidence']) > 1000:
        raise NarrativeError('invalid_evidence')
    known = set()
    for item in manifest['evidence']:
        if (type(item) is not dict or type(item.get('id')) is not str
                or not _ID.fullmatch(item['id']) or item['id'] in known
                or item.get('day') != day or type(item.get('source')) is not str or item.get('source') not in {'personal', 'work'}
                or (item['source'] == 'work' and item.get('sanitized') is not True)):
            raise NarrativeError('invalid_evidence')
        known.add(item['id'])
    paragraphs = entry['paragraphs']
    if type(paragraphs) is not list or len(paragraphs) > 30:
        raise NarrativeError('invalid_paragraphs')
    normalized = []
    for paragraph in paragraphs:
        if type(paragraph) is not dict or set(paragraph) != {'text', 'evidence_ids'}:
            raise NarrativeError('invalid_paragraph')
        text, ids = paragraph['text'], paragraph['evidence_ids']
        if (type(text) is not str or not text.strip() or text != text.strip()
                or len(text.encode('utf-8')) > 16000 or any(ord(c) < 32 for c in text)
                or re.match(r'(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>)', text)
                or '<!--' in text or '-->' in text or _BOILERPLATE.search(text)):
            raise NarrativeError('invalid_prose')
        if (type(ids) is not list or not ids or len(ids) > 200
                or any(type(item) is not str or item not in known for item in ids)
                or len(set(ids)) != len(ids)):
            raise NarrativeError('unknown_support')
        normalized.append({'text': text, 'evidence_ids': sorted(ids)})
    if len(_canonical(manifest).encode('utf-8')) > _MAX:
        raise NarrativeError('manifest_too_large')
    return {'version': 1, 'day': day, 'paragraphs': normalized}


def effective_manifest(entry, manifest):
    """Only referenced records affect retry identity; retain their exact metadata."""
    normalized = validate_entry(entry, manifest)
    used = {identity for paragraph in normalized['paragraphs'] for identity in paragraph['evidence_ids']}
    return {'version': 1, 'day': normalized['day'], 'evidence': sorted(
        (item for item in manifest['evidence'] if item['id'] in used), key=lambda item: item['id'])}


def render_entry(entry, block_id):
    return (f'<!-- journal-narrative:{block_id}:begin -->\n'
            + '\n\n'.join(p['text'] for p in entry['paragraphs'])
            + f'\n<!-- journal-narrative:{block_id}:end -->').encode('utf-8')


def _span(content, block_id):
    tokens = list(_MARKER.finditer(content))
    if content.count(b'<!-- journal-narrative:') != len(tokens):
        raise NarrativeError('malformed_markers')
    if not tokens:
        return None
    # One owner span per day. Unknown or duplicate spans require review.
    if (len(tokens) != 2 or tokens[0].group(1).decode() != block_id
            or tokens[1].group(1) != tokens[0].group(1)
            or tokens[0].group(2) != b'begin' or tokens[1].group(2) != b'end'):
        raise NarrativeError('review_required')
    return tokens[0].start(), tokens[1].end()


class NarrativePublisher:
    def __init__(self, gh_path, receipt_db, *, transport=None,
                 repository=REPOSITORY, branch=BRANCH, stop_path=None):
        if repository != REPOSITORY or branch != BRANCH:
            raise NarrativeError('destination')
        self.publisher = Publisher(gh_path, transport=transport)
        self.transport = StopAwareTransport(self.publisher.transport, stop_path)
        self.publisher.transport = self.transport
        self.path = Path(receipt_db).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS narrative_receipts (
                day TEXT PRIMARY KEY, block_id TEXT NOT NULL, state TEXT NOT NULL,
                body BLOB NOT NULL, body_hash TEXT NOT NULL, prior_body BLOB,
                input_digest TEXT NOT NULL, entry_json TEXT NOT NULL,
                receipt_json TEXT, last_error TEXT)''')

            db.execute('''CREATE TABLE IF NOT EXISTS narrative_attempts (
                day TEXT NOT NULL, input_digest TEXT NOT NULL, block_id TEXT NOT NULL,
                body BLOB NOT NULL, body_hash TEXT NOT NULL, entry_json TEXT NOT NULL,
                state TEXT NOT NULL, receipt_json TEXT,
                PRIMARY KEY(day,input_digest))''')

            db.execute('''CREATE TABLE IF NOT EXISTS narrative_support (
                day TEXT NOT NULL, input_digest TEXT NOT NULL, manifest_json TEXT NOT NULL,
                PRIMARY KEY(day,input_digest))''')

    @staticmethod
    def _save_support(db, day, digest, manifest):
        body = _canonical(manifest)
        existing = db.execute('SELECT manifest_json FROM narrative_support WHERE day=? AND input_digest=?',
                              (day, digest)).fetchone()
        if existing is not None and existing['manifest_json'] != body:
            raise NarrativeError('support_conflict')
        db.execute('INSERT OR IGNORE INTO narrative_support VALUES(?,?,?)', (day, digest, body))
        db.commit()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    def _read(self, path):
        try:
            return self.publisher._read(path)
        except PublishError as exc:
            raise NarrativeError(exc.code) from None

    @staticmethod
    def _review(db, day):
        db.execute("UPDATE narrative_receipts SET state='review_required',last_error='review_required' WHERE day=?", (day,))
        db.commit()
        raise NarrativeError('review_required')

    def publish(self, entry, manifest):
        self.transport.check()
        entry = validate_entry(entry, manifest)
        manifest = effective_manifest(entry, manifest)
        if not entry['paragraphs']:
            return {'state': 'skipped', 'day': entry['day']}
        # Separate SQLite write lock serializes local publishers while allowing
        # durable receipt commits before each remote attempt. Crash rolls it back.
        lock = sqlite3.connect(str(self.path) + '.lock.sqlite3', timeout=10)
        db = self._connect()
        try:
            lock.execute('BEGIN IMMEDIATE')
            return self._publish(db, entry, manifest)
        finally:
            db.close()
            lock.rollback()
            lock.close()

    def _publish(self, db, entry, manifest):
        day = entry['day']
        digest = _hash(_canonical({'entry': entry, 'manifest': manifest}))
        row = db.execute('SELECT * FROM narrative_receipts WHERE day=?', (day,)).fetchone()
        if row and row['state'] == 'review_required':
            raise NarrativeError('review_required')
        if row and row['state'] == 'pending' and row['input_digest'] != digest:
            raise NarrativeError('pending_input')
        self._save_support(db, day, digest, manifest)
        block_id = row['block_id'] if row else uuid.uuid4().hex
        body = render_entry(entry, block_id)
        path = f'entries/{day[:4]}/{day[5:7]}/{day}.md'
        for attempt in range(3):
            remote = self._read(path)
            content = remote['content'] if remote else b''
            try:
                span = _span(content, block_id)
            except NarrativeError:
                if row:
                    self._review(db, day)
                raise
            current_body = content[span[0]:span[1]] if span else None
            if row:
                if _hash(row['body']) != row['body_hash']:
                    self._review(db, day)
                if row['state'] == 'delivered':
                    if current_body != row['body']:
                        self._review(db, day)
                    if row['input_digest'] == digest:
                        return {'state': 'reconciled', 'day': day, 'block_id': block_id,
                                'body_hash': row['body_hash'], 'input_digest': digest,
                                'blob_sha': remote['sha']}
                    old = json.loads(row['entry_json'])['paragraphs']
                    new = entry['paragraphs']
                    if len(new) <= len(old) or new[:len(old)] != old:
                        raise NarrativeError('non_append_update')
                    old_ids = {i for p in old for i in p['evidence_ids']}
                    if any(not (set(p['evidence_ids']) - old_ids) for p in new[len(old):]):
                        raise NarrativeError('non_append_update')
                    prior = row['body']
                else:
                    if current_body == row['body']:
                        return self._delivered(db, day, block_id, body, digest, remote, None, True)
                    # A disappeared span for a pending update is a deletion;
                    # a first attempt may legitimately have no span yet.
                    if row['last_error'] == 'attempting' or current_body != row['prior_body']:
                        self._review(db, day)
                    prior = row['prior_body']
            else:
                if span:
                    raise NarrativeError('review_required')
                prior = None
            # Full immutable candidate and its input digest are durable before
            # networking, including the ambiguous-success crash window.
            db.execute('''INSERT OR REPLACE INTO narrative_receipts VALUES
                (?,?,?,?,?,?,?,?,?,?)''', (day, block_id, 'pending', body, _hash(body), prior,
                digest, _canonical(entry), None, 'attempting'))
            db.execute('INSERT OR IGNORE INTO narrative_attempts VALUES(?,?,?,?,?,?,?,?)',
                       (day, digest, block_id, body, _hash(body), _canonical(entry), 'pending', None))
            db.commit()
            row = db.execute('SELECT * FROM narrative_receipts WHERE day=?', (day,)).fetchone()
            if span:
                candidate = content[:span[0]] + body + content[span[1]:]
            else:
                original = content if remote else f'# {day}\n'.encode('ascii')
                candidate = original + b'\n\n' + body + b'\n'
            if len(candidate) > 8 * _MAX:
                raise NarrativeError('remote_data')
            failure, receipt = None, None
            try:
                self.publisher._verify()
                receipt = self.transport.write(path, candidate, remote['sha'] if remote else None)
            except NarrativeError as exc:
                if str(exc) == 'stopped':
                    # The guard rejected this call before transport.write began;
                    # retaining attempting would misclassify resume as ambiguity.
                    db.execute('UPDATE narrative_receipts SET last_error=? WHERE day=?', ('stopped', day))
                    db.commit()
                raise
            except PublishError as exc:
                failure = exc.code
            with self.transport.acknowledge():
                checked = self._read(path)
            checked_content = checked['content'] if checked else b''
            try:
                checked_span = _span(checked_content, block_id)
            except NarrativeError:
                self._review(db, day)
            checked_body = checked_content[checked_span[0]:checked_span[1]] if checked_span else None
            if checked_body == body:
                return self._delivered(db, day, block_id, body, digest, checked, receipt, bool(failure))
            if checked_body != prior:
                self._review(db, day)
            db.execute('UPDATE narrative_receipts SET last_error=? WHERE day=?', (failure or 'verification', day))
            db.execute('INSERT OR IGNORE INTO narrative_attempts VALUES(?,?,?,?,?,?,?,?)',
                       (day, digest, block_id, body, _hash(body), _canonical(entry), 'pending', None))
            db.commit()
            row = db.execute('SELECT * FROM narrative_receipts WHERE day=?', (day,)).fetchone()
            if failure == 'conflict' and attempt < 2:
                continue
            raise NarrativeError(failure or 'verification')
        raise NarrativeError('conflict')

    @staticmethod
    def _delivered(db, day, block_id, body, digest, remote, write, reconciled):
        receipt = {'state': 'reconciled' if reconciled else 'published', 'day': day,
                   'block_id': block_id, 'body_hash': _hash(body), 'input_digest': digest,
                   'blob_sha': remote['sha']}
        if isinstance(write, dict) and isinstance(write.get('commit_sha'), str) and re.fullmatch(r'[0-9a-f]{40,64}', write['commit_sha']):
            receipt['commit_sha'] = write['commit_sha']
        db.execute("UPDATE narrative_receipts SET state='delivered',receipt_json=?,last_error=NULL WHERE day=?", (_canonical(receipt), day))
        db.execute("UPDATE narrative_attempts SET state='delivered',receipt_json=? WHERE day=? AND input_digest=?", (_canonical(receipt), day, digest))
        db.commit()
        return receipt
