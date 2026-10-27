import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from journal_collector.store import Store
from journal_collector.work_summary import SummaryQueue, classify, journal_day


class WorkSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.tmp.name) / 'source.sqlite'
        self.queue = Path(self.tmp.name) / 'queue.sqlite'
        self.store = Store(self.source)
        self.number = 0

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def add(self, text='Testing a SECRET-PATIENT-123 change', native='one', **changes):
        self.number += 1
        item = dict(native_id=native, conversation_id='private-path', role='user',
                    text=text, complete=True, source_time='2026-10-27T14:00:00Z',
                    observed_at='2026-10-27T14:01:00Z',
                    provenance={'origin': 'human', 'timestamp_valid': True})
        item.update(changes)
        self.store.ingest_batch(dict(batch_id=str(self.number), source='claude-code',
                                    cursor_key='file', cursor={}, coverage={'status': 'partial'},
                                    messages=[item]))

    def test_dst_wall_clock(self):
        self.assertEqual('2026-03-08', journal_day('2026-03-08T08:30:00Z'))
        self.assertEqual('2026-03-07', journal_day('2026-03-08T07:30:00Z'))
        self.assertEqual('2026-10-31', journal_day('2026-11-01T07:30:00Z'))
        self.assertEqual('2026-11-01', journal_day('2026-11-01T09:00:00Z'))

    def test_privacy_idempotence_and_source_jobs_untouched(self):
        self.add()
        with SummaryQueue(self.source, self.queue) as q:
            q.prepare()
            first = q.ready()
            self.assertEqual(['topic_testing'], first[0]['facts'])
            self.assertEqual({'fragment_id', 'day', 'facts', 'coverage'}, set(first[0]))
            self.assertNotIn('SECRET', json.dumps(first))
            q.prepare()
            self.assertEqual(first, q.ready())
            q.delivered(first[0]['fragment_id'], {'state': 'published', 'commit': 'abc'})
            self.add(native='two')
            q.prepare()
            self.assertEqual([], q.ready())
        with SummaryQueue(self.source, self.queue) as q:
            q.prepare()
            self.assertEqual([], q.ready())
        self.assertEqual(2, len(self.store.pending_jobs()))

    def test_reactions_conservative_negation_quotes_plans_sarcasm(self):
        for text in ['I am not frustrated', 'If I am frustrated, I will stop',
                     'She said "I am frustrated"', 'I will be frustrated',
                     'I am frustrated /s', 'I am frustrated, just kidding',
                     'I am frustrated? No.', 'I was frustrated yesterday']:
            self.assertNotIn('reaction_frustrated', classify(text, 'user', True, {'origin': 'human'}))
        self.assertIn('reaction_frustrated', classify('I am frustrated.', 'user', True, {'origin': 'human'}))
        self.assertNotIn('reaction_frustrated', classify('I am frustrated.', 'assistant', True, {'origin': 'human'}))
        self.assertNotIn('reaction_frustrated', classify('I am frustrated.', 'user', False, {'origin': 'human'}))

    def test_context_incomplete_and_processing_exclusions(self):
        self.add(complete=False, provenance={'origin': 'uncertain', 'timestamp_valid': True})
        self.add('Testing the work summary collector', native='maintenance')
        self.add('Testing synthetic probe', native='probe')
        self.add('Testing', native='unsupported', complete=False,
                 provenance={'timestamp_valid': True, 'unsupported_content': True})
        with SummaryQueue(self.source, self.queue) as q:
            q.prepare()
            self.assertEqual(['topic_testing'], q.ready()[0]['facts'])
            self.assertEqual(1, q.status()['counts']['ready'])

    def test_exclusion_invalidates_pending(self):
        self.add()
        with SummaryQueue(self.source, self.queue) as q:
            q.prepare()
            self.store.exclude(self.store.messages()[0]['message_key'])
            self.assertEqual([], q.ready())
            self.assertEqual(1, q.status()['counts']['invalidated'])

    def test_delivered_revision_change_requires_review(self):
        self.add()
        with SummaryQueue(self.source, self.queue) as q:
            q.prepare()
            item = q.ready()[0]
            q.delivered(item['fragment_id'], {'state': 'published'})
            self.add('Writing a note')
            q.prepare()
            self.assertEqual(1, q.status()['counts']['review_required'])
            self.assertEqual([], q.ready())

    def test_failure_retry_and_exact_ack(self):
        self.add()
        with SummaryQueue(self.source, self.queue) as q:
            q.prepare()
            item = q.ready()[0]
            q.failed(item['fragment_id'], 'network_error')
            self.assertEqual(item, q.ready()[0])
            with self.assertRaises(ValueError):
                q.delivered('unknown', {'state': 'published'})
            self.add('Writing a note')
            self.assertEqual({'state': 'review_required'}, q.delivered(item['fragment_id'], {'state': 'published'}))
            self.assertEqual(1, q.status()['counts']['review_required'])

    def test_source_read_only_and_no_bodies_in_queue(self):
        self.add()
        with SummaryQueue(self.source, self.queue) as q:
            q.prepare()
            with self.assertRaises(sqlite3.OperationalError):
                q.source.execute('DELETE FROM messages')
            dump = '\n'.join(q.connection.iterdump())
            self.assertNotIn('SECRET-PATIENT', dump)
            self.assertNotIn('private-path', dump)

    def test_bounded_scan_does_not_starve_unprocessed_rows(self):
        for number in range(6):
            self.add('Testing' if number == 5 else 'Nothing classifiable', native=str(number))
        with SummaryQueue(self.source, self.queue) as q:
            for _ in range(6):
                self.assertLessEqual(q.prepare(limit=1)['scanned'], 1)
            self.assertEqual(1, len(q.ready()))
            self.assertEqual(6, q.status()['counts']['processed_revisions'])

    def test_pending_revision_retires_old_id(self):
        self.add()
        with SummaryQueue(self.source, self.queue) as q:
            q.prepare()
            old_id = q.ready()[0]['fragment_id']
            self.add('Writing documentation')
            q.prepare()
            ready = q.ready()
            self.assertEqual(1, len(ready))
            self.assertNotEqual(old_id, ready[0]['fragment_id'])
            self.assertEqual(['topic_writing'], ready[0]['facts'])

    def test_metadata_only_source_change_is_not_hidden_by_revision(self):
        self.add()
        with SummaryQueue(self.source, self.queue) as q:
            q.prepare()
            old_id = q.ready()[0]['fragment_id']
            self.add(source_time='2026-10-28T14:00:00Z')
            self.assertEqual(1, self.store.messages()[0]['revision'])
            q.prepare()
            ready = q.ready()
            self.assertEqual('2026-10-28', ready[0]['day'])
            self.assertNotEqual(old_id, ready[0]['fragment_id'])

    def test_crash_after_remote_success_then_source_revision_requires_review(self):
        self.add()
        with SummaryQueue(self.source, self.queue) as q:
            q.prepare()
            item = q.ready()[0]
            self.assertTrue(q.begin_publish(item['fragment_id']))
            # Remote appended successfully; process dies before delivered().
        self.add('Writing documentation')
        with SummaryQueue(self.source, self.queue) as q:
            q.prepare()
            self.assertEqual([], q.ready())
            self.assertEqual(1, q.status()['counts']['review_required'])
            row = q.connection.execute('SELECT fragment_id,state FROM fragments').fetchall()
            self.assertEqual([(item['fragment_id'], 'review_required')], [tuple(value) for value in row])

    def test_attempt_survives_failed_transport_and_restart_with_same_id(self):
        self.add()
        with SummaryQueue(self.source, self.queue) as q:
            q.prepare()
            item = q.ready()[0]
            self.assertTrue(q.begin_publish(item['fragment_id']))
            q.failed(item['fragment_id'], 'network')
            self.assertEqual(1, q.status()['counts']['publishing'])
        with SummaryQueue(self.source, self.queue) as q:
            self.assertEqual([item], q.ready())
            self.assertTrue(q.begin_publish(item['fragment_id']))
            self.assertEqual({'state': 'delivered'}, q.delivered(item['fragment_id'], {'state': 'reconciled'}))
            self.assertFalse(q.begin_publish(item['fragment_id']))
            self.assertFalse(q.begin_publish('unknown'))

    def test_begin_publish_rechecks_before_marking_attempt(self):
        self.add()
        with SummaryQueue(self.source, self.queue) as q:
            q.prepare()
            item = q.ready()[0]
            self.store.exclude(self.store.messages()[0]['message_key'])
            self.assertFalse(q.begin_publish(item['fragment_id']))
            self.assertEqual(1, q.status()['counts']['invalidated'])

    def test_attempt_source_changes_during_remote_then_receipt_is_retained(self):
        self.add()
        with SummaryQueue(self.source, self.queue) as q:
            q.prepare()
            item = q.ready()[0]
            q.begin_publish(item['fragment_id'])
            self.add('Writing documentation')
            self.assertEqual({'state': 'review_required'},
                             q.delivered(item['fragment_id'], {'state': 'published', 'commit_sha': 'a' * 40}))
            receipt = json.loads(q.connection.execute('SELECT receipt FROM fragments').fetchone()[0])
            self.assertEqual('a' * 40, receipt['commit_sha'])


if __name__ == '__main__':
    unittest.main()
