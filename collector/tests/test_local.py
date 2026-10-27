import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest

from journal_collector.local import collect_local

STAMP = '2026-10-26T19:00:00Z'

class MemoryStore:
    def __init__(self):
        self.cursors = {}
        self.batches = []
    def get_cursor(self, source, key):
        return copy.deepcopy(self.cursors.get((source, key)))
    def ingest_batch(self, batch):
        self.batches.append(copy.deepcopy(batch))
        if batch['coverage']['status'] not in {'error', 'authentication_required'}:
            self.cursors[(batch['source'], batch['cursor_key'])] = copy.deepcopy(batch['cursor'])
        return {'new':len(batch['messages']), 'revised':0, 'unchanged':0, 'incomplete':0, 'excluded':0}


def codex(text, id='m1', **extra):
    return {'type':'response_item', 'timestamp':STAMP, 'payload':{'type':'message','role':'user','id':id,'content':[{'type':'input_text','text':text}], **extra}}

def claude(text, **extra):
    return {'type':'user', 'uuid':'c1','sessionId':'session','timestamp':STAMP,'origin':{'kind':'human'},'message':{'role':'user','content':text}, **extra}

class LocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = MemoryStore()
    def write(self, name, records):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b''.join((json.dumps(r, ensure_ascii=False)+'\n').encode() for r in records))
        return path
    def scan(self, source='codex', **kwargs):
        return collect_local(self.store, codex_roots=[self.root] if source=='codex' else [], claude_roots=[self.root] if source=='claude' else [], **kwargs)
    def messages(self):
        return [m for b in self.store.batches for m in b['messages']]
    def test_exact_long_text_and_no_repeat(self):
        body = ' start\n' + 'Unicode 🐈\n' * 10000 + 'end '
        self.write('a.jsonl', [codex(body)])
        result = self.scan()
        self.assertEqual(self.messages()[0]['text'], body)
        self.assertEqual(self.messages()[0]['native_id'], 'm1')
        self.assertTrue(self.messages()[0]['complete'])
        self.assertEqual(result['status'], 'complete')
        self.scan()
        self.assertEqual(len(self.messages()), 1)
    def test_append_partial_and_bounded_large_line(self):
        path = self.write('a.jsonl', [codex('x'*3000)])
        raw = path.read_bytes()
        path.write_bytes(raw[:-1])
        for _ in range(30):
            result = self.scan(max_bytes=200)
        self.assertFalse(self.messages())
        self.assertEqual(result['status'], 'partial')
        with path.open('ab') as f:
            f.write(b'\n')
        self.scan(max_bytes=200)
        self.assertEqual(self.messages()[0]['text'], 'x'*3000)
    def test_replacement_same_length_restarts(self):
        path = self.write('a.jsonl', [codex('old')])
        self.scan()
        path.write_bytes(path.read_bytes().replace(b'old', b'new'))
        self.scan()
        self.assertEqual([m['text'] for m in self.messages()], ['old','new'])
    def test_all_files_fair_and_backlog(self):
        for i in range(8):
            self.write(f'{i}.jsonl', [codex('x'*500, str(i))])
        first = self.scan(max_bytes=120)
        self.assertEqual(first['files_discovered'], 8)
        self.assertEqual(first['status'], 'partial')
        for _ in range(60):
            last = self.scan(max_bytes=120)
        self.assertEqual(len(self.messages()), 8)
        self.assertEqual(last['status'], 'complete')
    def test_codex_mirrors_subagent_and_mixed_metadata(self):
        self.write('a.jsonl', [
            {'type':'event_msg','payload':{'type':'user_message','message':'mirror'}},
            codex('<environment_context>machine data</environment_context>\nactual human'),
            codex('secret reasoning','r', channel='analysis'),
            codex('<environment_context>machine only</environment_context>', 'h'),
        ])
        self.write('sub.jsonl', [{'type':'session_meta','payload':{'id':'s','source':{'subagent':'review'}}}, codex('agent', 'a')])
        self.scan()
        self.assertEqual([m['text'] for m in self.messages()], ['actual human'])
    def test_claude_human_wrappers_and_nonhuman(self):
        self.write('a.jsonl', [claude('<system-reminder>injected</system-reminder>\nmy text'), claude('nonhuman', uuid='c2', origin={'kind':'agent'}), claude('summary',uuid='c3', isCompactSummary=True), claude('side',uuid='c4',isSidechain=True), claude('tool',uuid='c5',message={'role':'user','content':[{'type':'tool_result','content':'tool'}]})])
        self.scan('claude')
        self.assertEqual([m['text'] for m in self.messages()], ['my text'])
    def test_unknown_origin_timestamp_and_content_incomplete(self):
        self.write('a.jsonl', [claude('uncertain', origin={'kind':'future'}), claude('missing stamp', uuid='c2', timestamp=None), claude('image',uuid='c3',message={'role':'user','content':[{'type':'text','text':'caption'}, {'type':'image','source':{}}]})])
        result = self.scan('claude')
        self.assertEqual(len(self.messages()), 3)
        self.assertTrue(all(not m['complete'] for m in self.messages()))
        self.assertEqual(result['status'], 'partial')
    def test_malformed_visible_and_not_silently_complete(self):
        path = self.write('a.jsonl', [codex('good')])
        with path.open('ab') as f:
            f.write(b'not json\n')
        result = self.scan()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['malformed_lines'], 1)
        result = self.scan()
        self.assertEqual(result['status'], 'partial')
    def test_since_discovery_and_native_copy_identity(self):
        for name in ('a.jsonl','copy.jsonl'):
            self.write(name, [codex('same')])
        self.scan(since='2026-10-25T00:00:00Z')
        self.assertEqual([m['native_id'] for m in self.messages()], ['m1','m1'])
    def test_budget_smaller_than_file_count_is_fair(self):
        for i in range(3):
            self.write(f'{i}.jsonl', [codex('body', str(i))])
        for _ in range(3):
            result = self.scan(max_bytes=1)
            self.assertLessEqual(result['bytes_read'], 1)
        self.assertTrue(all(c['read_offset'] > 0 for c in self.store.cursors.values()))
    def test_store_failure_does_not_acknowledge_cursor(self):
        self.write('a.jsonl', [codex('body')])
        original = self.store.ingest_batch
        def failed(batch):
            raise RuntimeError('simulated transactional failure')
        self.store.ingest_batch = failed
        with self.assertRaises(RuntimeError):
            self.scan()
        self.assertFalse(self.store.cursors)
        self.store.ingest_batch = original
        self.scan()
        self.assertEqual(len(self.messages()), 1)
    def test_real_store_restart_native_dedup_and_null_timestamp(self):
        from journal_collector.store import Store
        self.write('a.jsonl', [codex('native body')])
        self.write('copy.jsonl', [codex('native body')])
        missing = codex('unknown time', 'missing')
        missing['timestamp'] = None
        self.write('missing.jsonl', [missing])
        db = self.root / 'local.sqlite'
        with Store(db) as store:
            result = collect_local(store, codex_roots=[self.root], claude_roots=[])
            self.assertEqual(len(store.messages()), 2)
            self.assertEqual(len(store.pending_jobs()), 1)
        with Store(db) as store:
            result = collect_local(store, codex_roots=[self.root], claude_roots=[])
            self.assertEqual(result['messages_observed'], 0)
            self.assertEqual(len(store.messages()), 2)
            self.assertEqual(result['status'], 'partial')
    def test_changed_horizon_rescans_and_sources_unchanged(self):
        path = self.write('a.jsonl', [codex('body')])
        raw = path.read_bytes()
        self.scan(since='2026-10-27T00:00:00Z')
        self.assertFalse(self.messages())
        self.scan(since='2026-10-25T00:00:00Z')
        self.assertEqual(len(self.messages()), 1)
        self.assertEqual(path.read_bytes(), raw)
    def test_bad_source_identifiers_do_not_poison_valid_lines(self):
        from journal_collector.store import Store
        bad_ids = ['bad\x00id', 'x' * 1025, {'wrong': 'type'}]
        self.write('a.jsonl', [codex('first', 'first')] +
                   [codex('invalid', native) for native in bad_ids] + [codex('last', 'last')])
        with Store(self.root / 'local.sqlite') as store:
            result = collect_local(store, codex_roots=[self.root], claude_roots=[])
            self.assertEqual({m['text'] for m in store.messages()}, {'first', 'last'})
            self.assertEqual(result['malformed_lines'], 3)
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['backlog_bytes'], 0)
    def test_bad_conversation_and_timestamp_normalization(self):
        from journal_collector.store import Store
        self.write('a.jsonl', [claude('first',uuid='first'),
            claude('bad session', uuid='bad', sessionId='bad\x00session'),
            claude('bad type', uuid='badtype', sessionId=123),
            claude('normalized', uuid='last', timestamp='2026-10-26 19:00:00+00:00')])
        with Store(self.root / 'local.sqlite') as store:
            result = collect_local(store, codex_roots=[], claude_roots=[self.root])
            messages = {m['native_id']: m for m in store.messages()}
            self.assertEqual(set(messages), {'first', 'last'})
            self.assertEqual(messages['last']['source_time'], '2026-10-26T19:00:00+00:00')
            self.assertTrue(messages['last']['complete'])
            self.assertEqual(result['malformed_lines'], 2)
    def test_absent_native_id_has_stable_fallback(self):
        item = codex('no native ID')
        item['payload'].pop('id')
        self.write('a.jsonl', [item])
        self.scan()
        identity = self.messages()[0]['native_id']
        self.assertTrue(identity.startswith('fallback:'))
        self.store.cursors.clear()
        self.scan()
        self.assertEqual(self.messages()[1]['native_id'], identity)
    def test_disabled_sources(self):
        result = collect_local(self.store, codex_roots=[], claude_roots=[])
        self.assertEqual(result['files_discovered'], 0)
        self.assertEqual(result['status'], 'complete')

if __name__ == '__main__':
    unittest.main()
