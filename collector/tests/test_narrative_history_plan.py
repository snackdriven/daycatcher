import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from zoneinfo import ZoneInfo

from journal_collector.store import Store
from narrative_history_plan import fresh_state, scan_source, select_days, acknowledge, stopped, work_inventory


class NarrativeHistoryTests(unittest.TestCase):
    def setUp(self):
        try: ZoneInfo('America/Chicago')
        except Exception: self.skipTest('Supply pinned IANA data via PYTHONTZPATH')
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name); self.db = self.data / 'collector.sqlite3'
        self.config = {'data_dir': str(self.data), 'source_db': str(self.db)}
        self.store = Store(self.db); self.addCleanup(self.store.close)
        self.state = fresh_state()
        self.n = 0

    def add(self, day='2020-01-01', **extra):
        self.n += 1
        message = {'native_id': str(self.n), 'conversation_id': 'private-chat', 'role': 'user',
                   'text': 'I wanted a walk.', 'source_time': day+'T15:00:00Z', 'observed_at': '2026-10-27T15:00:00Z',
                   'complete': True, 'provenance': {'adapter': 'codex-local-jsonl-v1', 'origin': 'native-user-role', 'timestamp_valid': True}, **extra}
        self.store.ingest_batch({'batch_id': str(self.n), 'source': 'codex-local', 'cursor_key': 'live',
                                 'cursor': {}, 'coverage': {'status': 'partial'}, 'messages': [message]})
        return self.store.connection.execute('SELECT message_key FROM messages WHERE native_id=?', (message['native_id'],)).fetchone()[0]

    def scan(self, **kwargs): return scan_source(self.config, self.state, **kwargs)

    def test_old_days_two_per_pass_unchanged_and_late_reopen(self):
        for day in ('2020-01-01', '2020-01-02', '2020-01-03'): self.add(day)
        self.scan(); self.assertEqual(len(select_days(self.state, '2026-10-27')), 2)
        before = copy.deepcopy(self.state['days']); self.scan(); self.assertEqual(before, self.state['days'])
        self.state['days']['2020-01-01']['state'] = 'unchanged'
        self.add(); self.scan(); self.assertEqual(self.state['days']['2020-01-01']['state'], 'pending')

    def test_excluded_undated_and_conversation_exclusions_never_eligible(self):
        key = self.add(); self.store.exclude(key)
        self.add(source_time=None)
        self.add('2020-01-02', conversation_id='excluded')
        self.config['narrative_excluded_conversations'] = ['excluded']
        self.scan(); self.assertEqual(self.state['days'], {})

    def test_cutoff_uses_original_instant_and_scan_resumes(self):
        self.add(source_time='2020-01-02T08:30:00Z')
        self.add('2020-01-03')
        self.assertFalse(self.scan(max_bytes=1)['scan_complete'])
        self.assertEqual(self.state['days'], {})
        while not self.scan(max_bytes=2000)['scan_complete']: pass
        self.assertIn('2020-01-01', self.state['days'])

    def proof(self, day, outcome, **extra):
        inputs = self.data/'narrative-writer'/'inputs'; inputs.mkdir(parents=True, exist_ok=True)
        path = inputs/(day+'.json')
        if not path.exists(): path.write_text(json.dumps({'version': 1, 'day': day, 'nextOffset': None, 'coverage': {'omitted': 0}, 'existing_sha': 'b'*40}))
        return {'version': 1, 'day': day, 'fingerprint': self.state['days'][day]['fingerprint'],
                'outcome': outcome, 'input_digest': hashlib.sha256(path.read_bytes()).hexdigest(), 'reviewed': outcome != 'prepared',
                'result': {'state': outcome}, **extra}

    def test_ack_requires_current_fingerprint_and_prepared_reviewed_stages(self):
        self.add(); self.scan(); day = '2020-01-01'
        with self.assertRaises(ValueError): acknowledge(self.config, self.state, self.proof(day, 'unchanged'))
        acknowledge(self.config, self.state, self.proof(day, 'prepared'))
        self.assertEqual(self.state['days'][day]['state'], 'prepared')
        acknowledge(self.config, self.state, self.proof(day, 'reviewed'))
        proof = self.proof(day, 'unchanged', reason='no_meaningful_new_material',
                           remote={'path': 'entries/2020/01/2020-01-01.md', 'state': 'existing', 'blob_sha': 'b'*40})
        acknowledge(self.config, self.state, proof)
        self.assertEqual(self.state['days'][day]['state'], 'unchanged')
        proof['fingerprint'] = 'f'*64
        with self.assertRaises(ValueError): acknowledge(self.config, self.state, proof)

    def test_publication_requires_actual_delivered_receipt_and_deletion_gap_is_sticky(self):
        self.add(); self.scan(); day = '2020-01-01'
        for phase in ('prepared', 'reviewed'): acknowledge(self.config, self.state, self.proof(day, phase))
        with self.assertRaises(ValueError): acknowledge(self.config, self.state, self.proof(day, 'published'))
        acknowledge(self.config, self.state, self.proof(day, 'gap', reason='manual_deletion'))
        self.add(); self.scan()
        self.assertEqual(self.state['days'][day]['state'], 'gap')
        self.assertEqual(select_days(self.state, '2026-10-27'), [])

    def test_all_stop_controls(self):
        for path in (self.data/'narrative-STOP', self.data/'history'/'STOP', self.data/'narrative-history'/'STOP'):
            path.parent.mkdir(exist_ok=True); path.touch()
            self.assertTrue(stopped(self.config)); path.unlink()
        self.assertFalse(stopped(self.config))

    def test_work_inventory_is_metadata_only_and_work_only_days_plan(self):
        days = work_inventory({'version': 1, 'days': [{'day': '2020-01-01', 'fingerprint': 'c'*64}]})
        self.state['work_days'] = days; self.scan()
        self.assertTrue(select_days(self.state, '2026-10-27')[0]['work_available'])
        for row in ({'day': '2020-01-01', 'fingerprint': 'c'*64, 'text': 'forbidden'}, {'day': '2020-02-30', 'fingerprint': 'c'*64}):
            with self.assertRaises(ValueError): work_inventory({'version': 1, 'days': [row]})

    def test_changed_preparation_or_unread_pages_cannot_settle(self):
        self.add(); self.scan(); day = '2020-01-01'
        proof = self.proof(day, 'prepared'); proof['input_digest'] = 'f'*64
        with self.assertRaises(ValueError): acknowledge(self.config, self.state, proof)
        path = self.data/'narrative-writer'/'inputs'/(day+'.json')
        bundle = json.loads(path.read_text()); bundle['nextOffset'] = 200
        path.write_text(json.dumps(bundle))
        for phase in ('prepared', 'reviewed'): acknowledge(self.config, self.state, self.proof(day, phase))
        proof = self.proof(day, 'unchanged', reason='already_represented', remote={'path': 'entries/2020/01/2020-01-01.md', 'state': 'existing', 'blob_sha': 'b'*40})
        with self.assertRaises(ValueError): acknowledge(self.config, self.state, proof)

    def test_published_ack_checks_matching_reviewed_candidate_and_real_receipt(self):
        self.add(); self.scan(); day = '2020-01-01'; candidate = 'd'*64
        acknowledge(self.config, self.state, self.proof(day, 'prepared'))
        acknowledge(self.config, self.state, self.proof(day, 'reviewed', candidate_digest=candidate))
        db = sqlite3.connect(self.data/'narrative-writer'/'receipts.sqlite3')
        db.execute('CREATE TABLE narrative_receipts(day TEXT,state TEXT,input_digest TEXT,receipt_json TEXT,body BLOB,body_hash TEXT)')
        body = b'exact owned span'; body_hash = hashlib.sha256(body).hexdigest()
        # The original delivery's whole-file SHA differs after an unrelated
        # unowned edit/legacy cleanup; exact owned-span reconciliation is valid.
        receipt = {'blob_sha': 'f'*40}
        db.execute('INSERT INTO narrative_receipts VALUES(?,?,?,?,?,?)', (day, 'delivered', candidate, json.dumps(receipt), body, body_hash)); db.commit(); db.close()
        proof = self.proof(day, 'published', candidate_digest=candidate,
            result={'state': 'published', 'input_digest': candidate, 'blob_sha': 'e'*40, 'body_hash': body_hash},
            remote={'path': 'entries/2020/01/2020-01-01.md', 'state': 'existing', 'blob_sha': 'e'*40})
        acknowledge(self.config, self.state, proof)
        self.assertEqual(self.state['days'][day]['state'], 'published')

    def test_live_inserts_and_updates_between_every_partial_pass_do_not_starve(self):
        for _ in range(10): self.add()
        complete = False
        for attempt in range(10):
            result = self.scan(max_bytes=1500)
            if result['scan_complete']:
                complete = True; break
            self.add('2020-01-02')
            self.add(native_id='1', text='I wanted a walk '+str(attempt))
        self.assertTrue(complete, 'live activity must not reset the historical frontier')
        self.assertTrue(result['source_changed_during_scan'])
        self.assertEqual(self.state['days']['2020-01-01']['count'], 10)
        self.assertNotIn('2020-01-02', self.state['days'])
        self.scan()
        self.assertIn('2020-01-02', self.state['days'])

    def test_absent_entry_with_prior_path_history_cannot_ack_unchanged(self):
        self.add(); self.scan(); day = '2020-01-01'
        path = self.data/'narrative-writer'/'inputs'/(day+'.json')
        self.proof(day, 'prepared'); bundle = json.loads(path.read_text()); bundle['existing_sha'] = None; path.write_text(json.dumps(bundle))
        for phase in ('prepared', 'reviewed'): acknowledge(self.config, self.state, self.proof(day, phase))
        proof = self.proof(day, 'unchanged', reason='already_represented', remote={
            'path': 'entries/2020/01/2020-01-01.md', 'state': 'absent', 'path_history_checked': True, 'path_history_count': 1})
        with self.assertRaises(ValueError): acknowledge(self.config, self.state, proof)
