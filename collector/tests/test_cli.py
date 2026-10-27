import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

PACKAGE = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / 'runtime'
        self.db = self.data / 'collector.sqlite3'
        self.config = self.data / 'collector.json'

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, *args, value=None, ok=True):
        result = subprocess.run([sys.executable, '-m', 'journal_collector', *map(str, args)],
            input=json.dumps(value) if value is not None else None,
            capture_output=True, text=True, cwd=PACKAGE, timeout=15)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        return result

    def init(self):
        self.call('init', '--data-dir', self.data)
        config = json.loads(self.config.read_text())
        config['local'] = {'codex_roots': [], 'claude_roots': [], 'since': '2000-01-01T00:00:00Z'}
        self.config.write_text(json.dumps(config))

    def batch(self):
        return {'batch_id': 'cli-test-1', 'source': 'test-source', 'cursor_key': 'file',
            'cursor': {'offset': 42}, 'coverage': {'status': 'complete', 'detail': 'synthetic'},
            'messages': [{'native_id': 'msg1', 'conversation_id': 'chat1', 'role': 'user',
                'text': 'PRIVATE_SYNTHETIC_BODY', 'source_time': '2026-10-26T19:00:00Z',
                'observed_at': '2026-10-26T19:01:00Z', 'complete': True,
                'provenance': {'adapter': 'test'}}]}

    def test_init_ingest_cursor_and_metadata_status_across_processes(self):
        self.init()
        first = self.call('ingest', '--db', self.db, value=self.batch())
        self.assertEqual(first['new'], 1)
        self.call('ingest', '--db', self.db, value=self.batch())
        state = self.call('status', '--db', self.db)
        self.assertEqual(state['counts']['messages'], 1)
        self.assertEqual(state['counts']['pending_jobs'], 1)
        self.assertNotIn('PRIVATE_SYNTHETIC_BODY', json.dumps(state))
        self.assertEqual(self.call('cursor', '--db', self.db, '--source', 'test-source', '--key', 'file'), {'offset': 42})

    def test_preview_hides_bodies_unless_explicit_and_exclusion_survives_retry(self):
        self.init()
        self.call('ingest', '--db', self.db, value=self.batch())
        metadata = self.call('preview', '--db', self.db)
        self.assertNotIn('PRIVATE_SYNTHETIC_BODY', json.dumps(metadata))
        full = self.call('preview', '--db', self.db, '--text')
        self.assertIn('PRIVATE_SYNTHETIC_BODY', json.dumps(full))
        key = full[0]['message_key']
        self.call('exclude', '--db', self.db, '--message-key', key)
        later = self.batch(); later['batch_id'] = 'cli-test-2'
        self.call('ingest', '--db', self.db, value=later)
        self.assertNotIn('PRIVATE_SYNTHETIC_BODY', json.dumps(self.call('preview', '--db', self.db, '--text')))

    def test_scan_reads_native_source_and_repeat_produces_no_duplicate(self):
        self.init()
        native = self.root / 'codex'; native.mkdir()
        lines = [
            {'type': 'session_meta', 'payload': {'id': 'session1', 'source': 'cli'}},
            {'timestamp': '2026-10-26T19:00:00Z', 'type': 'response_item', 'payload': {
                'type': 'message', 'id': 'u1', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'synthetic user'}]}},
            {'timestamp': '2026-10-26T19:00:01Z', 'type': 'response_item', 'payload': {
                'type': 'message', 'id': 'a1', 'role': 'assistant', 'channel': 'final', 'content': [{'type': 'output_text', 'text': 'synthetic answer'}]}},
            {'timestamp': '2026-10-26T19:00:01Z', 'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': 'synthetic answer'}}]
        (native / 'session1.jsonl').write_text('\n'.join(json.dumps(x) for x in lines) + '\n')
        self.call('scan', '--config', self.config, '--codex-root', native)
        self.call('scan', '--config', self.config, '--codex-root', native)
        state = self.call('status', '--db', self.db)
        self.assertEqual(state['counts']['messages'], 2)
        self.assertEqual(state['counts']['pending_jobs'], 2)

    def test_bounded_worker_stops_and_emits_no_message_bodies(self):
        self.init()
        result = self.call('watch', '--config', self.config, '--max-scans', '2', '--interval', '0.01')
        self.assertEqual(result['scans'], 2)
        health = json.loads((self.data / 'local-status.json').read_text())
        self.assertEqual(health['state'], 'stopped')
        self.assertEqual(health['reason'], 'max_scans')

    def test_background_stop_file_and_single_worker_lock(self):
        self.init()
        process = subprocess.Popen([sys.executable, '-m', 'journal_collector', 'watch', '--config', str(self.config), '--interval', '0.05'],
            cwd=PACKAGE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 8
            status = self.data / 'local-status.json'
            while not status.exists() and time.monotonic() < deadline:
                time.sleep(0.03)
            self.assertTrue(status.exists(), 'worker did not start')
            second = self.call('watch', '--config', self.config, '--max-scans', '1', ok=False)
            self.assertNotEqual(second.returncode, 0)
            (self.data / 'STOP').write_text('stop test')
            stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(json.loads(stdout)['reason'], 'stop_file')
        finally:
            if process.poll() is None:
                process.terminate(); process.communicate(timeout=5)

    def test_missing_configuration_does_not_create_implicit_database(self):
        result = self.call('status', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('data', result.stderr.lower())

    def test_nonfinite_and_boolean_intervals_are_rejected_before_worker_start(self):
        self.init()
        result = self.call('watch', '--config', self.config, '--max-scans', '1', '--interval', 'nan', ok=False)
        self.assertNotEqual(result.returncode, 0)
        for value in (float('nan'), float('inf'), True):
            config = json.loads(self.config.read_text())
            config['poll_seconds'] = value
            self.config.write_text(json.dumps(config))
            result = self.call('watch', '--config', self.config, '--max-scans', '1', ok=False)
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
