from contextlib import closing
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from journal_collector.store import Store


def batch(batch_id='b1', text='original', complete=True, native_id='n1', conversation='c1'):
    return {'batch_id': batch_id, 'source': 'test-source', 'cursor_key': 'file',
            'cursor': {'offset': 10}, 'coverage': {'status': 'complete'},
            'messages': [{'native_id': native_id, 'conversation_id': conversation,
                          'role': 'user', 'text': text, 'complete': complete,
                          'source_time': '2026-10-26T19:00:00Z',
                          'observed_at': '2026-10-26T19:01:00Z', 'provenance': {}}]}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.sqlite'
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_restart_retry_and_conflicting_retry(self):
        first = self.store.ingest_batch(batch())
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(first, self.store.ingest_batch(batch()))
        with self.assertRaises(ValueError):
            self.store.ingest_batch(batch(text='changed'))
        self.assertEqual('original', self.store.messages()[0]['text'])
        self.assertEqual(1, len(self.store.pending_jobs()))
        self.assertEqual({'offset': 10}, self.store.get_cursor('test-source', 'file'))

    def test_repeat_associations_revision_and_incomplete_preservation(self):
        self.store.ingest_batch(batch())
        self.assertEqual(1, self.store.ingest_batch(batch('b2', conversation='c2'))['unchanged'])
        self.assertEqual(1, len(self.store.pending_jobs()))
        self.assertEqual({'c1', 'c2'}, set(self.store.messages()[0]['conversation_ids']))
        self.assertEqual(1, self.store.ingest_batch(batch('b3', text=None, complete=False))['incomplete'])
        self.assertEqual('original', self.store.messages()[0]['text'])
        self.assertTrue(self.store.messages()[0]['complete'])
        self.assertEqual(1, self.store.ingest_batch(batch('b4', text='changed'))['revised'])
        self.assertEqual(2, self.store.messages()[0]['revision'])
        self.assertEqual(2, len(self.store.pending_jobs()))

    def test_incomplete_then_complete(self):
        result = self.store.ingest_batch(batch(text=None, complete=False))
        self.assertEqual(1, result['incomplete'])
        self.assertEqual([], self.store.pending_jobs())
        self.assertEqual(0, self.store.messages()[0]['revision'])
        self.store.ingest_batch(batch('b2'))
        self.assertEqual(1, self.store.messages()[0]['revision'])
        self.assertEqual(1, len(self.store.pending_jobs()))

    def test_exclusion_scrubs_every_body_and_survives_restart(self):
        self.store.ingest_batch(batch())
        key = self.store.messages()[0]['message_key']
        self.store.ingest_batch(batch('b2', text='revision-secret'))
        self.store.exclude(key)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(1, self.store.ingest_batch(batch('b3', text='reimport-secret'))['excluded'])
        self.assertEqual([], self.store.messages())
        self.assertEqual([], self.store.pending_jobs())
        with closing(sqlite3.connect(self.path)) as conn:
            dump = '\n'.join(conn.iterdump())
        for body in ['original', 'revision-secret', 'reimport-secret']:
            self.assertNotIn(body, dump)
        self.assertEqual(1, self.store.status()['counts']['exclusions'])

    def test_invalid_message_rolls_back_entire_batch_and_cursor(self):
        bad = batch()
        bad['messages'].append(dict(bad['messages'][0], native_id='n2', source_time='not-a-date'))
        with self.assertRaises(ValueError):
            self.store.ingest_batch(bad)
        self.assertEqual([], self.store.messages())
        self.assertEqual([], self.store.pending_jobs())
        self.assertIsNone(self.store.get_cursor('test-source', 'file'))
        self.assertEqual(0, self.store.status()['counts']['batches'])

    def test_sql_failure_rolls_back_messages_jobs_and_cursor(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("CREATE TRIGGER fail_job BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.ingest_batch(batch())
        self.assertEqual([], self.store.messages())
        self.assertEqual([], self.store.pending_jobs())
        self.assertIsNone(self.store.get_cursor('test-source', 'file'))
        self.assertEqual(0, self.store.status()['counts']['batches'])

    def test_error_auth_do_not_advance_and_status_has_no_body(self):
        self.store.ingest_batch(batch())
        for number, state in enumerate(['error', 'authentication_required']):
            value = batch('error' + str(number))
            value.update(messages=[], cursor={'offset': 999}, coverage={'status': state})
            self.store.ingest_batch(value)
            self.assertEqual({'offset': 10}, self.store.get_cursor('test-source', 'file'))
        self.assertNotIn('original', json.dumps(self.store.status()))

    def test_source_identity_is_separate_and_timestamps_validated(self):
        self.store.ingest_batch(batch())
        value = batch('b2')
        value['source'] = 'other'
        self.store.ingest_batch(value)
        self.assertEqual(2, len({m['message_key'] for m in self.store.messages()}))
        for field, timestamp in [('source_time', '2026-10-26T19:00:00'), ('observed_at', None)]:
            value = batch('bad-' + field)
            value['messages'][0][field] = timestamp
            with self.assertRaises(ValueError):
                self.store.ingest_batch(value)

    def test_two_writers_same_identity_serialize(self):
        barrier = threading.Barrier(2)
        failures = []
        def run(number):
            try:
                with Store(self.path) as store:
                    barrier.wait(timeout=5)
                    store.ingest_batch(batch('writer-' + str(number)))
            except Exception as exc:
                failures.append(exc)
        threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual([], failures)
        self.assertEqual(1, len(self.store.messages()))
        self.assertEqual(1, len(self.store.pending_jobs()))


if __name__ == '__main__':
    unittest.main()
