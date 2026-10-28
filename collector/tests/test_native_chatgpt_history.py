import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from native_chatgpt_history import new_state, discover, plan, import_page, load_owned_state
from journal_collector.chatgpt_native import normalize_page
from journal_collector.store import Store


class HistoryTests(unittest.TestCase):
    def listing(self):
        return {'schemaVersion': 4, 'threads': [{'id': 'old', 'kind': 'chatgpt', 'updatedAt': 1000}], 'pinnedThreads': []}

    def page(self, **extra):
        return {'schemaVersion': 1, 'thread': {'id': 'old', 'kind': 'chatgpt'}, 'page': {'order': 'newest_first', 'limit': 10, 'hasMore': True, 'nextCursor': 'older'}, 'turns': [{'id': 'm', 'startedAt': 1000, 'items': [{'id': 'm', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'fixture'}]}]}], **extra}

    def test_old_discovery_and_archive_cursor_binding(self):
        state = new_state()
        discover(state, self.listing(), 'active')
        self.assertEqual(plan(state)[0]['threadId'], 'old')
        self.assertFalse(state['active_end_proven'])
        discover(state, {'threads': [], 'nextCursor': 'next'}, 'archive')
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            discover(state, {'threads': [], 'nextCursor': None}, 'archive', cursor='wrong')
        self.assertEqual(state, before)
        discover(state, {'threads': [], 'nextCursor': None}, 'archive', cursor='next')
        self.assertTrue(state['archive_end_proven'])

    def test_title_truncation_does_not_reject_supported_inventory_identity(self):
        payload = self.listing()
        payload['threads'][0].update(title='private display title', titleTruncated=True)
        state = new_state(); discover(state, payload, 'active')
        self.assertEqual(len(plan(state)), 1)
        self.assertNotIn('private display title', json.dumps(state))
        with self.assertRaises(ValueError): discover(state, {**payload, 'truncated': True}, 'active')

    def test_import_uses_preservation_and_separate_cursor_after_success(self):
        state = new_state(); discover(state, self.listing(), 'active')
        calls = []
        def ingest(batch, **policy):
            calls.append((batch, policy)); return {'new': 1, 'batch_id': batch['batch_id']}
        import_page(state, 'old', None, self.page(), ingest)
        self.assertEqual(plan(state)[0]['cursor'], 'older')
        self.assertTrue(calls[0][0]['cursor_key'].startswith('history:'))
        self.assertEqual(calls[0][1], {'preserve_existing_complete': True})
        self.assertEqual(calls[0][0]['messages'][0]['source_time'], '1970-01-01T00:16:40+00:00')

    def test_failed_or_truncated_page_never_advances_and_settles_gap(self):
        state = new_state(); discover(state, self.listing(), 'active')
        for _ in range(3):
            import_page(state, 'old', None, self.page(truncated=True), lambda *a, **k: self.fail('ingested'))
        self.assertEqual(state['threads']['old']['cursor'], None)
        self.assertEqual(state['threads']['old']['status'], 'gap')
        self.assertEqual(plan(state), [])

    def test_store_failure_and_wrong_cursor_do_not_ack(self):
        state = new_state(); discover(state, self.listing(), 'active')
        def fail(*a, **k): raise RuntimeError('failure')
        import_page(state, 'old', None, self.page(), fail)
        self.assertIsNone(state['threads']['old']['cursor'])
        with self.assertRaises(ValueError): import_page(state, 'old', 'wrong', self.page(), fail)

    def test_plan_limit_and_page_end_proof(self):
        state = new_state(); value = self.listing(); value['threads'] *= 7
        value['threads'] = [{**r, 'id': str(i)} for i, r in enumerate(value['threads'])]
        discover(state, value, 'active'); self.assertEqual(len(plan(state, 50)), 5)
        state = new_state(); discover(state, self.listing(), 'active')
        page = self.page(); page['page'].update(hasMore=False, nextCursor=None)
        import_page(state, 'old', None, page, lambda b, **k: {'new': 1, 'batch_id': b['batch_id']})
        self.assertEqual(state['threads']['old']['status'], 'exhausted')
        self.assertEqual(plan(state), [])

    def test_real_store_stale_copy_and_crash_replay(self):
        with tempfile.TemporaryDirectory() as temp, Store(Path(temp) / 'source.sqlite3') as store:
            fresh = self.page(); fresh['turns'][0]['items'][0]['content'][0]['text'] = 'fresh canonical text'
            batch = normalize_page(fresh, expected_thread_id='old', since='1900-01-01T00:00:00Z',
                                   until='2100-01-01T00:00:00Z', now='2026-10-27T00:00:00Z')
            store.ingest_batch(batch)
            before = dict(store.connection.execute('SELECT * FROM messages').fetchone())
            state = new_state(); discover(state, self.listing(), 'active')
            saved = []
            def lose_receipt(batch, **policy):
                store.ingest_batch(batch, **policy)
                raise RuntimeError('receipt lost after commit')
            import_page(state, 'old', None, self.page(), lose_receipt,
                        lambda: saved.append(copy.deepcopy(state)))
            # Reconstruct exactly the pre-commit persisted checkpoint.
            state = saved[-1]
            result = import_page(state, 'old', None, self.page(), store.ingest_batch)
            self.assertEqual(result['state'], 'imported')
            self.assertEqual(result['counts']['preserved'], 1)
            self.assertEqual(dict(store.connection.execute('SELECT * FROM messages').fetchone()), before)
            self.assertEqual(store.get_cursor('chatgpt-native', batch['cursor_key']), batch['cursor'])

    def test_cli_uses_separate_files_and_stop_without_database_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            config = data / 'collector.json'
            config.write_text(json.dumps({'data_dir': str(data), 'db': str(data / 'collector.sqlite3')}))
            live = data / 'native-chatgpt-schedule.json'; live.write_text('existing-live-checkpoint')
            listing = data / 'active.json'; listing.write_text(json.dumps(self.listing()))
            script = Path(__file__).resolve().parents[1] / 'native_chatgpt_history.py'
            def call(*args):
                result = subprocess.run([sys.executable, str(script), '--config', str(config), *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return json.loads(result.stdout)
            self.assertEqual(call('--active', str(listing))['discovered'], 1)
            self.assertEqual(call('--plan')['reads'][0]['threadId'], 'old')
            (data / 'native-chatgpt-STOP').touch()
            self.assertEqual(call('--plan')['state'], 'stopped')
            (data / 'native-chatgpt-STOP').unlink()
            (data / 'native-chatgpt-history-STOP').touch()
            self.assertEqual(call('--plan')['state'], 'stopped')
            self.assertEqual(live.read_text(), 'existing-live-checkpoint')
            self.assertFalse((data / 'collector.sqlite3').exists())

    def test_cumulative_owned_state_larger_than_external_page_limit_reloads(self):
        with tempfile.TemporaryDirectory() as temp:
            state = new_state()
            for i in range(15000):
                state['threads'][str(i)] = {'cursor': None, 'status': 'exhausted', 'attempts': 0, 'pages': 1, 'order': i, 'oldest_verified_source_time': '2020-01-01T00:00:00+00:00'}
            path = Path(temp)/'state.json'; path.write_text(json.dumps(state))
            self.assertGreater(path.stat().st_size, 2*1024*1024)
            self.assertEqual(load_owned_state(path), state)
