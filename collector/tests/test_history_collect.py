import copy
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from journal_collector.store import Store
from journal_collector.local import collect_local
from tests.test_store import batch


class PreservationTests(unittest.TestCase):
    def test_live_transaction_wins_before_history_reads_canonical_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'db'
            with Store(path) as live:
                live.ingest_batch(batch())
                live.connection.execute('BEGIN IMMEDIATE')
                live.connection.execute("UPDATE messages SET text='fresh live' WHERE native_id='n1'")
                outcome = []
                started = threading.Event()
                def historical():
                    with Store(path) as history:
                        started.set()
                        outcome.append(history.ingest_batch(batch('history:race', text='stale'),
                                                            preserve_existing_complete=True))
                thread = threading.Thread(target=historical)
                thread.start()
                self.assertTrue(started.wait(5))
                time.sleep(0.05)
                self.assertEqual([], outcome)
                live.connection.execute('COMMIT')
                thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(1, outcome[0]['preserved'])
                self.assertEqual('fresh live', live.messages()[0]['text'])
    def test_history_preserves_every_canonical_field_and_jobs_and_associations(self):
        with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp)/'db') as store:
            store.ingest_batch(batch())
            before = {t: list(map(tuple, store.connection.execute('SELECT * FROM '+t)))
                      for t in ('messages', 'associations', 'revisions', 'jobs')}
            stale = batch('history:stale', text='stale', conversation='other')
            stale['messages'][0].update(role='assistant', source_time='2020-01-01T00:00:00Z',
                                        observed_at='2020-01-01T00:01:00Z', provenance={'stale': True})
            result = store.ingest_batch(stale, preserve_existing_complete=True)
            self.assertEqual(1, result['preserved'])
            self.assertEqual(1, result['conflicting'])
            for table, rows in before.items():
                self.assertEqual(rows, list(map(tuple, store.connection.execute('SELECT * FROM '+table))))
            self.assertEqual(result, store.ingest_batch(stale, preserve_existing_complete=True))
            with self.assertRaises(ValueError):
                store.ingest_batch(stale)

    def test_history_improves_incomplete_and_respects_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp)/'db') as store:
            store.ingest_batch(batch(complete=False, text=None))
            result = store.ingest_batch(batch('history:complete'), preserve_existing_complete=True)
            self.assertEqual(1, result['revised'])
            key = store.messages()[0]['message_key']
            store.exclude(key)
            result = store.ingest_batch(batch('history:excluded'), preserve_existing_complete=True)
            self.assertEqual(1, result['excluded'])
            self.assertEqual([], store.messages())

    def test_history_cursors_are_separate_but_file_provenance_is_canonical(self):
        with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp)/'db') as store:
            root = Path(tmp)
            record = {'type':'response_item', 'timestamp':'2020-01-01T00:00:00Z',
                      'payload':{'type':'message','role':'user','id':'old','content':'old memory'}}
            (root/'session.jsonl').write_text(json.dumps(record)+'\n')
            collect_local(store, codex_roots=[root], claude_roots=[], since='2026-01-01T00:00:00Z')
            live = copy.deepcopy(list(map(tuple, store.connection.execute('SELECT * FROM cursors'))))
            result = collect_local(store, codex_roots=[root], claude_roots=[],
                                   cursor_namespace='history-local-v1', preserve_existing_complete=True)
            self.assertEqual(1, result['counts']['new'])
            self.assertEqual(live, list(map(tuple, store.connection.execute(
                "SELECT * FROM cursors WHERE cursor_key NOT LIKE 'history-local-v1:%'"))))
            self.assertEqual(live[0][1], store.messages()[0]['provenance']['file_key'])
            self.assertEqual(1, store.connection.execute(
                "SELECT COUNT(*) FROM batches WHERE batch_id LIKE 'history-local-v1:%'").fetchone()[0])
            self.assertEqual(0, collect_local(store, codex_roots=[root], claude_roots=[],
                cursor_namespace='history-local-v1', preserve_existing_complete=True)['messages_observed'])


class RunnerTests(unittest.TestCase):
    def test_large_multiline_source_remains_pending_then_reaches_eof(self):
        from journal_collector import history_collect
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'large.jsonl'
            line = json.dumps({'type':'nonmessage','unused':'x'*1024}).encode()+b'\n'
            source.write_bytes(line*17000)
            path = root/'collector.json'
            path.write_text(json.dumps({'data_dir':str(root/'data'),
                'local':{'codex_roots':[str(source)],'claude_roots':[]}}))
            first = history_collect.run_once(path)
            self.assertEqual(16*1024*1024, first['bytes_read'])
            self.assertEqual(1, first['queue']['pending'])
            self.assertEqual(0, first['queue']['gap'])
            second = history_collect.run_once(path)
            self.assertEqual(1, second['queue']['eof'])
            self.assertEqual(0, second['queue']['gap'])

    def test_disappeared_file_returns_to_pending_when_rediscovered(self):
        from journal_collector import history_collect
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'one.jsonl'
            source.write_text('{}\n')
            path = root/'collector.json'
            path.write_text(json.dumps({'data_dir':str(root/'data'),
                'local':{'codex_roots':[str(root)],'claude_roots':[]}}))
            history_collect.run_once(path)
            hidden = source.with_suffix('.hidden')
            source.rename(hidden)
            self.assertEqual(1, history_collect.run_once(path)['queue']['missing'])
            hidden.rename(source)
            self.assertEqual(1, history_collect.run_once(path)['files_scanned'])
    def test_watch_owns_history_lock_during_idle_and_honors_independent_stop(self):
        from journal_collector.runtime import local_is_locked
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root/'collector.json'
            path.write_text(json.dumps({'data_dir':str(root/'data'),
                'local':{'codex_roots':[],'claude_roots':[]}}))
            process = subprocess.Popen([sys.executable, 'history_collect.py', '--config',str(path),'--watch'],
                cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                deadline = time.monotonic()+5
                status = root/'data'/'history'/'status.json'
                while not status.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(status.exists())
                self.assertTrue(local_is_locked(root/'data'/'history'))
                (root/'data'/'history'/'STOP').touch()
                _, stderr = process.communicate(timeout=5)
                self.assertEqual(0, process.returncode, stderr)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=5)

    def test_missing_root_reports_unproven_discovery(self):
        from journal_collector import history_collect
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root/'collector.json'
            path.write_text(json.dumps({'data_dir':str(root/'data'),
                'local':{'codex_roots':[str(root/'missing')],'claude_roots':[]}}))
            result = history_collect.run_once(path)
            self.assertEqual('gaps', result['status'])
            self.assertFalse(result['discovery']['end_proven'])
    def test_oversized_indivisible_line_is_bounded_and_settles_as_gap(self):
        from journal_collector import history_collect
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'oversized.jsonl'
            source.write_bytes(b'{"untrusted":"' + b'x'*(17*1024*1024) + b'"}\n')
            path = root/'collector.json'
            path.write_text(json.dumps({'data_dir':str(root/'data'),
                'local':{'codex_roots':[str(source)],'claude_roots':[]}}))
            first = history_collect.run_once(path)
            self.assertEqual(16*1024*1024, first['bytes_read'])
            self.assertEqual(1, first['queue']['gap'])
            self.assertEqual(0, history_collect.run_once(path)['bytes_read'])

    def test_crash_after_canonical_commit_reconciles_queue_without_duplicate_jobs(self):
        from journal_collector import history_collect
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'one.jsonl'
            source.write_text(json.dumps({'type':'response_item','timestamp':'2020-01-01T00:00:00Z',
                'payload':{'type':'message','role':'user','id':'one','content':'body'}})+'\n')
            path = root/'collector.json'
            path.write_text(json.dumps({'data_dir':str(root/'data'),
                'local':{'codex_roots':[str(source)],'claude_roots':[]}}))
            real_collect = history_collect.collect_local
            def interrupted(*args, **kwargs):
                real_collect(*args, **kwargs)
                raise RuntimeError('simulated crash before queue checkpoint')
            with patch.object(history_collect, 'collect_local', side_effect=interrupted):
                with self.assertRaises(RuntimeError):
                    history_collect.run_once(path)
            result = history_collect.run_once(path)
            self.assertEqual(1, result['queue']['eof'])
            with Store(root/'data'/'collector.sqlite3') as store:
                self.assertEqual(1, len(store.messages()))
                self.assertEqual(1, len(store.pending_jobs()))

    def test_small_file_queue_resumes_ignores_live_stop_and_reports_only_counts(self):
        from journal_collector import history_collect
        from journal_collector.runtime import worker_lock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'sources'
            source.mkdir()
            for name, body in [('large', 'secret title '*500), ('small', 'private body')]:
                record = {'type':'response_item', 'timestamp':'2020-01-01T00:00:00Z',
                          'payload':{'type':'message','role':'user','id':name,'content':body}}
                (source/(name+'.jsonl')).write_text(json.dumps(record)+'\n')
            data = root/'private'
            data.mkdir()
            (data/'STOP').touch()
            config_path = root/'collector.json'
            config_path.write_text(json.dumps({'data_dir':str(data),
                'local':{'codex_roots':[str(source)], 'claude_roots':[], 'since':'2026-01-01T00:00:00Z'}}))
            config = history_collect.load_config(config_path)
            unchanged_config = config_path.read_bytes()
            with worker_lock(data):
                first = history_collect.run_once(config, max_files=1)
            self.assertEqual(1, first['queue']['pending'])
            with Store(data/'collector.sqlite3') as store:
                self.assertEqual('small', store.messages()[0]['native_id'])
            second = history_collect.run_once(config_path, max_files=1)
            self.assertEqual(2, second['queue']['eof'])
            self.assertEqual(0, second['queue']['pending'])
            third = history_collect.run_once(config_path)
            self.assertEqual(0, third['files_scanned'])
            self.assertEqual(unchanged_config, config_path.read_bytes())
            for value in ('secret title', 'private body', 'small.jsonl', str(source)):
                self.assertNotIn(value, json.dumps(first))
            (data/'history'/'STOP').touch()
            self.assertEqual('stopped', history_collect.run_once(config)['status'])

    def test_incomplete_last_line_settles_as_gap_and_append_reopens(self):
        from journal_collector import history_collect
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'one.jsonl'
            record = {'type':'response_item', 'timestamp':'2020-01-01T00:00:00Z',
                      'payload':{'type':'message','role':'user','id':'one','content':'body'}}
            source.write_text(json.dumps(record))
            config_path = root/'collector.json'
            config_path.write_text(json.dumps({'data_dir':str(root/'data'),
                'local':{'codex_roots':[str(source)], 'claude_roots':[]}}))
            first = history_collect.run_once(config_path)
            self.assertEqual(1, first['queue']['gap'])
            with source.open('a') as stream:
                stream.write('\n')
            second = history_collect.run_once(config_path)
            self.assertEqual(1, second['queue']['eof'])
            with Store(root/'data'/'collector.sqlite3') as store:
                self.assertEqual(1, len(store.messages()))

    def test_relative_config_refused(self):
        from journal_collector import history_collect
        with self.assertRaises(ValueError):
            history_collect.load_config(Path('relative.json'))


if __name__ == '__main__':
    unittest.main()
