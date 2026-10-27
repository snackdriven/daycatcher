import sqlite3
import tempfile
import unittest
from pathlib import Path

from journal_collector.store import Store
from journal_collector.personal_summary import PersonalSummaryQueue, classify, _eligible
from journal_collector.work_summary import SummaryQueue


class PersonalSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.tmp.name) / 'source.sqlite'
        self.queue = Path(self.tmp.name) / 'personal.sqlite'
        self.store = Store(self.source)
        self.number = 0

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def add(self, text='Learning to paint at home', source='codex-local', **changes):
        self.number += 1
        item = dict(native_id=str(self.number), conversation_id='PRIVATE-CONVERSATION',
                    role='user', text=text, complete=True,
                    source_time='2026-10-27T14:00:00Z', observed_at='2026-10-27T14:01:00Z',
                    provenance={'origin': 'native-user-role', 'timestamp_valid': True})
        item.update(changes)
        self.store.ingest_batch(dict(batch_id=str(self.number), source=source,
                                    cursor_key='private-file', cursor={},
                                    coverage={'status': 'partial'}, messages=[item]))
        return self.store.connection.execute('SELECT * FROM messages WHERE source=? AND native_id=?',
                                             (source, item['native_id'])).fetchone()

    def test_broad_personal_context_and_payload_privacy(self):
        self.add('Learning to paint at home. Shopping for books and watching movies. SECRET-NAME')
        with PersonalSummaryQueue(self.source, self.queue) as q:
            q.prepare()
            item = q.ready()[0]
            self.assertEqual(['topic_creativity', 'topic_entertainment', 'topic_home',
                              'topic_learning', 'topic_shopping'], item['facts'])
            self.assertEqual({'fragment_id', 'day', 'facts', 'coverage'}, set(item))
            self.assertEqual('partial', item['coverage'])
            dump = '\n'.join(q.connection.iterdump())
            for secret in ('SECRET-NAME', 'PRIVATE-CONVERSATION', 'private-file'):
                self.assertNotIn(secret, dump)
            with self.assertRaises(sqlite3.OperationalError):
                q.source.execute('DELETE FROM messages')

    def test_account_normalized_role_and_timestamp_without_jsonl_provenance(self):
        # Exact provenance contract emitted by normalizeConversation: sender=human
        # maps to user, with adapter identity but no origin/timestamp_valid fields.
        p = {'adapter': 'claude-chat-ambient-v1', 'parent_native_id': None,
             'source_updated_at': None, 'attachments': []}
        row = self.add('I am relieved.', source='claude-chat:' + 'a' * 20, provenance=p)
        self.assertEqual(('2026-10-27', ['reaction_relieved']), _eligible(row))
        row = self.add('I am relieved.', source='claude-chat:' + 'a' * 20,
                       provenance=p, role='assistant')
        self.assertEqual(('2026-10-27', []), _eligible(row))
        for changes in ({'complete': False}, {'source_time': None},
                        {'provenance': {'adapter': 'unverified'}},
                        {'provenance': {**p, 'unsupported_content': True}}):
            row = self.add('Learning', source='claude-chat:' + 'a' * 20,
                           **({'provenance': p} | changes))
            self.assertIsNone(_eligible(row))

    def test_account_naive_timestamp_rejected_even_if_store_corrupted(self):
        row = dict(self.add(source='claude-chat:' + 'b' * 20,
                            provenance={'adapter': 'claude-chat-ambient-v1'}))
        row['source_time'] = '2026-10-27T14:00:00'
        self.assertIsNone(_eligible(row))

    def test_allowlist_and_jsonl_conservative_origin_compatibility(self):
        for source in ('work-codex', 'claude-code', 'codex', 'claude-chat:unknown',
                       'claude-chat:', 'claude-chat:' + 'a' * 19):
            self.assertIsNone(_eligible(self.add(source=source)))
        for source in ('codex-local', 'claude-code-local'):
            self.assertIn('topic_learning', _eligible(self.add(source=source))[1])
        uncertain = self.add(complete=False, provenance={'origin': 'uncertain', 'timestamp_valid': True})
        self.assertIn('topic_home', _eligible(uncertain)[1])
        for p in ({'origin': 'human'}, {'origin': 'human', 'timestamp_valid': False},
                  {'origin': 'human', 'timestamp_valid': True, 'unsupported_content': True}):
            self.assertIsNone(_eligible(self.add(provenance=p)))

    def test_maintenance_and_synthetic_rows_abstain(self):
        for text in ('Planning personal summary', 'Planning shared journal',
                     'Planning work journal', 'Planning Day Crumbs',
                     'Learning synthetic probe', 'Building journal_collector',
                     'Planning personal-summary-service', 'Message Type: NEW_TASK Learning'):
            self.assertEqual([], classify(text, 'user', True, {'origin': 'human'}), text)
        for key in ('synthetic', 'processing_generated', 'relay', 'is_meta'):
            self.assertEqual([], classify('Learning', 'user', True, {key: True}))

    def test_no_sensitive_inference_and_strict_human_reactions(self):
        for text in ('My diagnosis is SECRET', 'My bank balance is SECRET',
                     'My partner left me', 'I must be tired', 'I am not tired',
                     'She said I am tired', 'I am tired /s', 'I was tired',
                     'I am tired?', 'If I am tired, I will sleep'):
            self.assertEqual([], classify(text, 'user', True, {'origin': 'human'}), text)
        self.assertEqual(['reaction_tired'], classify('I am tired.', 'user', True, {'origin': 'human'}))
        for role, complete, p in [('assistant', True, {'origin': 'human'}),
                                   ('user', False, {'origin': 'human'}),
                                   ('user', True, {'origin': 'uncertain'})]:
            self.assertEqual([], classify('I am tired.', role, complete, p))

    def test_profiles_reject_cross_use_and_legacy_work_queue(self):
        self.add()
        with PersonalSummaryQueue(self.source, self.queue) as q:
            q.prepare()
            first = q.ready()
        with self.assertRaisesRegex(ValueError, 'queue_profile_mismatch'):
            SummaryQueue(self.source, self.queue)
        with PersonalSummaryQueue(self.source, self.queue) as q:
            self.assertEqual(first, q.ready())
        legacy = Path(self.tmp.name) / 'legacy.sqlite'
        with SummaryQueue(self.source, legacy) as q:
            q.connection.execute("DELETE FROM meta WHERE key='namespace'")
            q.connection.commit()
        with self.assertRaisesRegex(ValueError, 'queue_profile_mismatch'):
            PersonalSummaryQueue(self.source, legacy)
        with SummaryQueue(self.source, legacy):
            pass

    def test_account_mutation_invalidates_and_publish_receipt_survives(self):
        self.add('Learning', source='claude-chat:' + 'c' * 20,
                 provenance={'adapter': 'claude-chat-ambient-v1'})
        with PersonalSummaryQueue(self.source, self.queue) as q:
            q.prepare()
            item = q.ready()[0]
            self.assertTrue(q.begin_publish(item['fragment_id']))
            q.failed(item['fragment_id'], 'network_error')
        with PersonalSummaryQueue(self.source, self.queue) as q:
            self.assertEqual([item], q.ready())
            self.store.exclude(self.store.messages()[0]['message_key'])
            self.assertEqual({'state': 'review_required'},
                             q.delivered(item['fragment_id'], {'state': 'published', 'commit': 'a' * 40}))
            self.assertEqual([], q.ready())
            self.assertEqual(1, q.status()['counts']['review_required'])

    def test_personal_durable_queue_dedupes_and_retires_revised_pending_id(self):
        self.add('Learning')
        with PersonalSummaryQueue(self.source, self.queue) as q:
            q.prepare()
            first = q.ready()[0]
            self.store.connection.execute("UPDATE messages SET text='Shopping',revision=revision+1")
            q.prepare()
            revised = q.ready()[0]
            self.assertNotEqual(first['fragment_id'], revised['fragment_id'])
            self.assertEqual(['topic_shopping'], revised['facts'])
            q.delivered(revised['fragment_id'], {'state': 'published'})
            self.add('Shopping')
            q.prepare()
            self.assertEqual([], q.ready())

    def test_source_allowlist_rechecked_before_publish(self):
        self.add('Learning')
        with PersonalSummaryQueue(self.source, self.queue) as q:
            q.prepare()
            first = q.ready()[0]
            self.store.connection.execute("UPDATE messages SET source='unknown'")
            self.assertFalse(q.begin_publish(first['fragment_id']))
            self.assertEqual([], q.ready())
            self.assertEqual(1, q.status()['counts']['invalidated'])


if __name__ == '__main__':
    unittest.main()
