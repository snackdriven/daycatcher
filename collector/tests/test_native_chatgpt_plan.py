"""Metadata-only planning, fairness and isolated runtime boundary tests."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'native_chatgpt_plan.py'
SYNTHETIC = '00000000-0000-4000-8000-000000000001'


class NativeChatGPTPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.config = self.data / 'collector.json'
        self.config.write_text(json.dumps({'data_dir': str(self.data),
                                           'db': str(self.data / 'collector.sqlite3')}))
        self.now = datetime.now(timezone.utc)
        self.input = self.data / 'list.json'

    def thread(self, ident, *, days=0, kind='chatgpt'):
        return {'id': ident, 'kind': kind,
                'updatedAt': (self.now - timedelta(days=days)).timestamp(),
                'title': 'PRIVATE TITLE DO NOT EMIT', 'summary': 'PRIVATE BODY DO NOT EMIT'}

    def listing(self, rows=None, pinned=None, **fields):
        value = {'schemaVersion': 4, 'threads': rows or [], 'pinnedThreads': pinned or []}
        value.update(fields)
        self.input.write_text(json.dumps(value))

    def call(self, *args, ok=True, config=None):
        result = subprocess.run([sys.executable, str(SCRIPT), '--config', str(config or self.config), *args],
                                capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('PRIVATE', result.stdout + result.stderr)
        return json.loads(result.stdout)

    def plan(self, *args, **kwargs):
        return self.call('--list', str(self.input), *args, **kwargs)

    def ack(self, ident, status='imported', stamp=None):
        return self.call('--ack', ident, '--status', status, '--observed-at', stamp or self.now.isoformat())

    def test_filters_pinned_deduplicates_and_declares_partial_coverage(self):
        self.listing([self.thread('recent'), self.thread('old', days=8), self.thread('local', kind='codex'),
                      self.thread(SYNTHETIC)], [self.thread('pinned'), self.thread('recent')])
        value = self.plan()
        self.assertEqual({p['thread_id'] for p in value['plan']}, {'recent', 'pinned'})
        self.assertEqual(value['coverage']['status'], 'partial')
        self.assertEqual(value['coverage']['turn_limit'], 10)
        self.assertEqual(value['coverage']['read_scope'], 'latest_10_turns_only')
        self.assertFalse(value['coverage']['account_complete'])
        self.assertFalse((self.data / 'native-chatgpt-schedule.json').exists())

    def test_ack_rotates_unseen_then_least_recently_checked(self):
        self.listing([self.thread(f'chat-{i}') for i in range(7)])
        first = self.plan()['plan']
        self.assertEqual(len(first), 5)
        for item in first:
            self.ack(item['thread_id'])
        second = self.plan()['plan']
        self.assertEqual([p['thread_id'] for p in second[:2]], ['chat-5', 'chat-6'])
        self.ack('chat-5', 'error', (self.now + timedelta(seconds=1)).isoformat())
        self.assertEqual(self.plan()['plan'][0]['thread_id'], 'chat-6')

    def test_only_success_advances_overlap_watermark_and_incomplete_still_rotates(self):
        self.listing([self.thread('chat')])
        good = self.now - timedelta(days=3)
        self.ack('chat', stamp=good.isoformat())
        self.ack('chat', 'incomplete')
        row = self.plan()['plan'][0]
        self.assertEqual(datetime.fromisoformat(row['since']), good - timedelta(hours=1))
        state = json.loads((self.data / 'native-chatgpt-schedule.json').read_text())
        self.assertEqual(state['threads']['chat']['status'], 'incomplete')
        self.assertNotIn('PRIVATE', json.dumps(state))

    def test_default_window_48_hours_and_success_window_bounded_to_14_days(self):
        self.listing([self.thread('chat')])
        row = self.plan()['plan'][0]
        self.assertEqual(datetime.fromisoformat(row['until']) - datetime.fromisoformat(row['since']),
                         timedelta(hours=48))
        self.ack('chat', stamp=(self.now - timedelta(days=40)).isoformat())
        row = self.plan()['plan'][0]
        self.assertEqual(datetime.fromisoformat(row['until']) - datetime.fromisoformat(row['since']),
                         timedelta(days=14))

    def test_stop_prevents_plan_and_ack_without_creating_state(self):
        self.listing([self.thread('chat')])
        (self.data / 'native-chatgpt-STOP').write_text('stop')
        self.assertEqual(self.plan()['state'], 'stopped')
        self.assertEqual(self.ack('chat')['state'], 'stopped')
        self.assertFalse((self.data / 'native-chatgpt-schedule.json').exists())

    def test_rejects_bad_schema_nonfinite_dates_ids_and_limits(self):
        for bad in [self.thread('bad/id'), self.thread('nan'), self.thread('bool')]:
            if bad['id'] == 'nan': bad['updatedAt'] = float('nan')
            if bad['id'] == 'bool': bad['updatedAt'] = True
            self.listing([bad])
            self.assertEqual(self.plan(ok=False)['state'], 'error')
        self.listing([self.thread('chat')], schemaVersion=3)
        self.plan(ok=False)
        self.listing([self.thread('chat')])
        self.plan('--limit', '6', ok=False)
        self.plan('--limit', '0', ok=False)

    def test_canonical_config_and_synthetic_ack_rejected(self):
        self.listing([self.thread('chat')])
        alias = self.data / 'other.json'
        alias.write_text(self.config.read_text())
        self.plan(config=alias, ok=False)
        self.call('--ack', SYNTHETIC, '--status', 'imported', '--observed-at', self.now.isoformat(), ok=False)
        self.call('--ack', 'chat', '--status', 'error', '--observed-at', '2026-10-27T00:00:00', ok=False)

    def test_separate_lock_does_not_conflict_with_collector(self):
        self.listing([self.thread('chat')])
        sys.path.insert(0, str(SCRIPT.parent))
        from journal_collector.runtime import worker_lock
        with worker_lock(self.data):
            self.ack('chat')
        with worker_lock(self.data / 'native-chatgpt-plan-lock'):
            self.call('--ack', 'chat', '--status', 'error', '--observed-at', self.now.isoformat(), ok=False)

    def test_malformed_state_fails_closed_and_old_ack_does_not_regress(self):
        self.listing([self.thread('chat')])
        self.ack('chat')
        self.call('--ack', 'chat', '--status', 'error',
                  '--observed-at', (self.now - timedelta(hours=1)).isoformat(), ok=False)
        state = self.data / 'native-chatgpt-schedule.json'
        state.write_text('{broken')
        self.plan(ok=False)


if __name__ == '__main__':
    unittest.main()
