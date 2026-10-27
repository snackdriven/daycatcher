import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from journal_collector.chatgpt_native import normalize_page
from journal_collector.store import Store

NOW = '2026-10-27T12:00:00Z'
SINCE = '2026-10-26T00:00:00Z'
UNTIL = NOW

def payload():
    return {'schemaVersion': 1, 'thread': {'id': 'chat-synthetic', 'kind': 'chatgpt'},
            'page': {'order': 'newest_first', 'limit': 4, 'nextCursor': None, 'hasMore': False}, 'turns': [
                {'id':'message-synthetic', 'startedAt': 1793098800, 'items': [{'type': 'userMessage', 'id': 'message-synthetic',
                 'content': [{'type': 'text', 'text': 'MEAT_SUIT synthetic private sentinel'}]},
                 {'type': 'agentMessage', 'text': '::chatgpt-content-reference private'}]}]}

class NativeTests(unittest.TestCase):
    def normalize(self, value=None, **kw):
        return normalize_page(value if value is not None else payload(),
            expected_thread_id='chat-synthetic', since=SINCE, until=UNTIL, now=NOW, **kw)

    def test_user_only_original_time_and_partial_coverage(self):
        batch = self.normalize()
        self.assertEqual(batch['source'], 'chatgpt-native')
        self.assertEqual(batch['coverage']['status'], 'partial')
        self.assertFalse(batch['coverage']['assistant_coverage'])
        self.assertEqual(batch['coverage']['assistant_unavailable'], 1)
        msg = batch['messages'][0]
        self.assertEqual(msg['native_id'], 'message-synthetic')
        self.assertEqual(msg['source_time'], '2026-10-27T11:00:00+00:00')
        self.assertEqual(msg['provenance'], {'adapter': 'chatgpt-native-v1', 'origin': 'human',
                         'timestamp_valid': True, 'user_only': True, 'known_probe': True, 'synthetic': True})
        self.assertNotIn('reference', json.dumps(batch))

    def test_truncation_any_level_fails_closed(self):
        for select in [lambda p:p, lambda p:p['thread'], lambda p:p['turns'][0],
                       lambda p:p['turns'][0]['items'][0],
                       lambda p:p['turns'][0]['items'][0]['content'][0]]:
            p=payload(); select(p)['truncated']=True
            with self.subTest(level=select):
                batch=self.normalize(p)
                self.assertEqual(batch['messages'], [])
                self.assertGreater(batch['coverage']['incomplete'],0)

    def test_unknown_content_and_bad_time_skip_without_prose(self):
        for change in ['attachment','contentref','badtime','booltime','infinity','missingid']:
            p=payload(); t=p['turns'][0]
            if change in ('attachment','contentref'):
                t['items'][0]['content'].append({'type':change,'text':'private'})
            elif change=='badtime': t['startedAt']='yesterday'
            elif change=='booltime': t['startedAt']=True
            elif change=='infinity': t['startedAt']=float('inf')
            else: t['items'][0]['id']=''
            batch=self.normalize(p)
            self.assertEqual(batch['messages'],[],change)
            self.assertEqual(batch['coverage']['incomplete'],1)

    def test_metadata_attachments_and_generated_users_excluded(self):
        for level,key,value in [('item','context_references',[{'text':'private'}]),
                                ('item','attachments',[{'id':'private'}]),
                                ('item','metadata',{'origin':'automation'}),
                                ('turn','systemGenerated',True),
                                ('response','attachments',[{'id':'private'}])]:
            p=payload()
            target=p if level=='response' else p['turns'][0] if level=='turn' else p['turns'][0]['items'][0]
            target[key]=value
            result=self.normalize(p)
            self.assertEqual(result['messages'],[],key)
            self.assertEqual(result['coverage']['incomplete'],1)

    def test_duplicate_item_and_page_continuation_do_not_claim_completeness(self):
        p=payload();p['turns'][0]['items'].append(dict(p['turns'][0]['items'][0]))
        p['page'].update(hasMore=True,nextCursor='opaque-private-cursor')
        batch=self.normalize(p)
        self.assertEqual(len(batch['messages']),1)
        self.assertEqual(batch['coverage']['incomplete'],1)
        self.assertEqual(batch['coverage']['status'],'partial')
        self.assertTrue(batch['cursor']['has_more'])

    def test_mismatched_turn_identity_and_user_contentref_rejected(self):
        for kind in ('mismatch','reference'):
            p=payload()
            if kind=='mismatch': p['turns'][0]['id']='different-turn'
            else: p['turns'][0]['items'][0]['content'][0]['text']='::chatgpt-content-reference{index="0"}'
            self.assertEqual(self.normalize(p)['messages'],[])

    def test_relay_meta_and_generated_at_each_envelope_level_are_rejected(self):
        flags=[{'relay':True}, {'is_meta':True}, {'processing_generated':True}, {'synthetic':True},
               {'metadata':{'relay':True}}, {'metadata':{'metadata':{'is_meta':True}}},
               {'author':{'role':'system'}}, {'author':{'role':'automation'}},
               {'author':{'role':'unknown'}}, {'author':'unknown'}]
        for level in ('response','thread','turn','item'):
            for flag in flags:
                p=payload()
                target={'response':p,'thread':p['thread'],'turn':p['turns'][0],
                        'item':p['turns'][0]['items'][0]}[level]
                target.update(flag)
                with self.subTest(level=level,flag=flag):
                    b=self.normalize(p)
                    self.assertEqual(b['messages'],[])
                    self.assertEqual(b['coverage']['incomplete'],1)

    def test_plain_text_is_not_metadata_and_known_human_author_is_allowed(self):
        p=payload();item=p['turns'][0]['items'][0]
        item['author']={'role':'user','id':'synthetic-user'}
        item['content'][0]['text']='I want to learn about automation and system design'
        b=self.normalize(p)
        self.assertEqual(len(b['messages']),1)
        self.assertEqual(b['messages'][0]['provenance']['origin'],'human')

    def test_bad_envelope_rejected(self):
        for p in [[], {}, {**payload(),'schemaVersion':2},
                  {**payload(),'thread':{'id':'wrong','kind':'chatgpt'}},
                  {**payload(),'page':[]}, {**payload(),'turns':'private'}]:
            with self.assertRaises(ValueError): self.normalize(p)

    def test_window_aware_and_empty_partial(self):
        p=payload(); p['turns'][0]['startedAt']=0
        self.assertEqual(self.normalize(p)['messages'],[])
        with self.assertRaises(ValueError):
            normalize_page(payload(),expected_thread_id='chat-synthetic',since='2026-10-26',until=UNTIL,now=NOW)

    def test_stable_identity_repeat_and_partial_cannot_erase_body(self):
        with tempfile.TemporaryDirectory() as td, Store(Path(td)/'db.sqlite3') as store:
            first=self.normalize(); self.assertEqual(store.ingest_batch(first)['new'],1)
            later=normalize_page(payload(),expected_thread_id='chat-synthetic',since=SINCE,until=UNTIL,
                                 now='2026-10-27T12:01:00Z')
            self.assertEqual(store.ingest_batch(later)['unchanged'],1)
            p=payload();p['turns'][0]['items'][0]['truncated']=True
            store.ingest_batch(self.normalize(p))
            self.assertEqual(store.status()['counts']['messages'],1)
            self.assertEqual(store.messages()[0]['revision'],1)

    def test_cli_dry_run_stop_and_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); config=root/'collector.json'; input_path=root/'input.json'
            config.write_text(json.dumps({'data_dir':str(root),'db':str(root/'collector.sqlite3')}))
            input_path.write_text(json.dumps(payload()))
            script=Path(__file__).resolve().parents[1]/'import_chatgpt_native.py'
            command=[sys.executable,str(script),'--config',str(config),'--input',str(input_path),
                     '--expected-thread','chat-synthetic','--since',SINCE,'--until',UNTIL]
            result=subprocess.run(command+['--dry-run'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((root/'collector.sqlite3').exists())
            self.assertNotIn('sentinel',result.stdout);self.assertNotIn('message-synthetic',result.stdout)
            imported=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(imported.returncode,0,imported.stdout)
            self.assertEqual(json.loads(imported.stdout)['counts']['new'],1)
            repeated=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(json.loads(repeated.stdout)['counts']['unchanged'],1)
            (root/'collector.sqlite3').unlink()
            (root/'native-chatgpt-STOP').touch()
            result=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(json.loads(result.stdout)['state'],'stopped')
            self.assertFalse((root/'collector.sqlite3').exists())

    def test_cli_wrong_binding_and_oversized_input_fail_without_database(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); config=root/'collector.json'; inp=root/'input.json'
            script=Path(__file__).resolve().parents[1]/'import_chatgpt_native.py'
            args=[sys.executable,str(script),'--config',str(config),'--input',str(inp),
                  '--expected-thread','chat-synthetic','--since',SINCE,'--until',UNTIL]
            for wrong in (False,True):
                config.write_text(json.dumps({'data_dir':str(root),
                    'db':str(root/('wrong.sqlite3' if wrong else 'collector.sqlite3'))}))
                inp.write_bytes(b' '*(2*1024*1024+1) if not wrong else b'{}')
                result=subprocess.run(args,capture_output=True,text=True)
                self.assertEqual(result.returncode,1)
                self.assertEqual(json.loads(result.stdout)['state'],'error')
                self.assertFalse(list(root.glob('*.sqlite3')))

if __name__=='__main__': unittest.main()
