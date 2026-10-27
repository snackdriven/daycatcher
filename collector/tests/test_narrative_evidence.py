import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from zoneinfo import ZoneInfo, reset_tzpath

from journal_collector.store import Store
from journal_collector.narrative_evidence import export_day, day_window, eligible_message


class NarrativeEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            ZoneInfo('America/Chicago')
        except Exception:
            import importlib.resources
            try:
                blob=importlib.resources.files('tzdata.zoneinfo').joinpath('America/Chicago').read_bytes()
            except ModuleNotFoundError:
                raise unittest.SkipTest('Supply installed IANA data via PYTHONTZPATH')
            cls.zone_tmp = tempfile.TemporaryDirectory()
            zone = Path(cls.zone_tmp.name) / 'America' / 'Chicago'
            zone.parent.mkdir()
            zone.write_bytes(blob)
            reset_tzpath([cls.zone_tmp.name])
            ZoneInfo.clear_cache()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'collector.sqlite3'
        self.store = Store(self.path)
        self.n = 0

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def add(self, text='I wanted to learn SQL.', source='codex-local', **changes):
        self.n += 1
        item = dict(native_id=str(self.n), conversation_id='private-chat', role='user',
                    text=text, source_time='2026-10-27T14:00:00Z',
                    observed_at='2026-10-27T14:01:00Z', complete=True,
                    provenance=dict(adapter='codex-local-jsonl-v1', origin='native-user-role', timestamp_valid=True))
        item.update(changes)
        self.store.ingest_batch(dict(batch_id=str(self.n), source=source, cursor_key='private-file',
                                    cursor={}, coverage={'status':'partial'}, messages=[item]))
        return dict(self.store.connection.execute('SELECT * FROM messages WHERE source=? AND native_id=?',
                                                  (source,item['native_id'])).fetchone())

    def test_supported_human_sources_retain_full_text_without_inference(self):
        self.add()
        self.add(source='claude-code-local', provenance=dict(adapter='claude-code-local-jsonl-v1',origin='human',timestamp_valid=True))
        self.add(source='claude-chat:'+'a'*20, provenance=dict(adapter='claude-chat-ambient-v1',attachments=[]))
        self.add(source='chatgpt-native', provenance=dict(adapter='chatgpt-native-v1',origin='human',timestamp_valid=True,user_only=True))
        result=export_day(self.path,'2026-10-27')
        self.assertEqual(4,len(result['evidence']))
        self.assertTrue(all(x['text']=='I wanted to learn SQL.' for x in result['evidence']))
        self.assertEqual('partial',result['coverage']['status'])
        self.assertNotIn('private-chat',json.dumps(result))
        self.assertNotIn(str(self.path),json.dumps(result))

    def test_assistant_uncertain_wrong_adapter_unsupported_and_metadata_rejected(self):
        base=self.add()
        invalid=[dict(role='assistant'),dict(complete=0),dict(source='unknown'),dict(source_time=None)]
        for provenance in ({},dict(adapter='codex-local-jsonl-v1',origin='uncertain',timestamp_valid=True),
                           dict(adapter='wrong',origin='native-user-role',timestamp_valid=True)):
            invalid.append(dict(provenance=json.dumps(provenance)))
        for flag in ('synthetic','processing_generated','relay','is_meta','unsupported_content','automation','isSidechain'):
            p=json.loads(base['provenance']);p[flag]=True
            invalid.append(dict(provenance=json.dumps(p)))
        p=json.loads(base['provenance']);p['metadata']={'origin':'agent'}
        invalid.append(dict(provenance=json.dumps(p)))
        p=json.loads(base['provenance']);p['metadata']={'source':'automation'}
        invalid.append(dict(provenance=json.dumps(p)))
        for change in invalid:
            self.assertIsNone(eligible_message(base|change),change)
        account=base|dict(source='claude-chat:'+'a'*20,provenance=json.dumps(dict(adapter='claude-chat-ambient-v1',attachments=[{'embedded_plain_text':True}])))
        self.assertIsNone(eligible_message(account))
        p=dict(adapter='claude-chat-ambient-v1',timestamp_valid=False)
        self.assertIsNone(eligible_message(account|dict(provenance=json.dumps(p))))

    def test_harness_quotes_pasted_code_and_exclusions_fail_closed_but_complaint_survives(self):
        for text in ('Message Type: NEW_TASK\nPayload: buy books',
                     '<environment_context>stuff</environment_context> I wanted a movie.',
                     '```python\nprint(1)\n```', '> Assistant said I felt relieved',
                     'Assistant: I finished the task.', 'This is an automated background-task event',
                     'Here is the transcript:\nuser: I bought it', 'Run `rm x` please'):
            self.add(text)
        self.add('I hate that the journal collector keeps losing my real complaints.')
        excluded=self.add('I watched a movie.')
        self.store.exclude(excluded['message_key'])
        result=export_day(self.path,'2026-10-27')
        self.assertEqual(['I hate that the journal collector keeps losing my real complaints.'],
                         [e['text'] for e in result['evidence']])

    def test_generated_speaker_script_and_agent_receipt_abstain_without_commit_keyword_ban(self):
        self.add('make this a prompt HOST: I would build a small garden. GUEST: I agree.')
        self.add('Created and reread imports/example.md on main.\nCommit: '+'a'*40+'\nRepository privacy verified.')
        self.add('I was annoyed that my last commit kept the old journal topic lists.')
        result=export_day(self.path,'2026-10-27')
        self.assertEqual(['I was annoyed that my last commit kept the old journal topic lists.'],
                         [e['text'] for e in result['evidence']])

    def test_obvious_credentials_abstain_without_mutating_source_or_losing_normal_feedback(self):
        for text in ('use sk-'+'SyntheticCanaryOnly123456789',
                     'Here is ghp_'+'SyntheticCanaryOnly123456789',
                     'github_pat_'+'SyntheticCanaryOnly123456789',
                     'Authorization: Bearer SyntheticCanaryOnly123456789',
                     'password=synthetic-only-example',
                     'API-key: "synthetic-only-example"',
                     'access_token = synthetic-only-example',
                     'My password is synthetic-only-example'):
            self.add(text)
        self.add('I changed my password today.')
        self.add('I hate that the journal keeps losing my complaints.')
        before=self.path.read_bytes()
        result=export_day(self.path,'2026-10-27')
        self.assertCountEqual(['I changed my password today.',
                               'I hate that the journal keeps losing my complaints.'],
                              [e['text'] for e in result['evidence']])
        self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(10,self.store.connection.execute('SELECT count(*) FROM messages').fetchone()[0])

    def test_model_content_references_and_relay_shapes_abstain(self):
        for text in ('Quoted result \ue200cite\ue202turn0search0\ue201',
                     'Copied contentReference[oaicite:0]{index=0}',
                     'See ::chatgpt-content-reference[example]',
                     'Message Type: MESSAGE\nSender: agent\nPayload: I finished it.',
                     '<heartbeat>Write a journal update</heartbeat>'):
            self.add(text)
        self.add('I was annoyed by the journal update.')
        self.assertEqual(['I was annoyed by the journal update.'],
                         [e['text'] for e in export_day(self.path,'2026-10-27')['evidence']])

    def test_source_coverage_is_bounded_per_source_and_accounting_separates_undated(self):
        self.add();self.add(role='assistant');self.add(source_time=None)
        for i in range(12):
            self.store.connection.execute('INSERT INTO cursors VALUES(?,?,?,?,?)',
                ('codex-local','file'+str(i),'{}',json.dumps({'status':'error' if i==0 else 'complete'}),
                 '2026-10-27T14:00:00Z'))
        result=export_day(self.path,'2026-10-27')
        coverage=result['coverage']
        self.assertEqual(coverage['scanned'],coverage['eligible']+coverage['rejected'])
        self.assertEqual(1,coverage['undatedRejected'])
        self.assertEqual(1,len(coverage['sourceCoverage']))
        self.assertEqual('error',coverage['sourceCoverage'][0]['status'])
        self.assertEqual(13,coverage['sourceCoverage'][0]['observations'])

    def test_chicago_cutoff_and_dst_windows(self):
        self.add(source_time='2026-10-27T07:59:59Z')
        self.add('I tried drawing.',source_time='2026-10-27T08:00:00Z')
        self.add(source_time='2026-10-28T08:00:00Z')
        self.assertEqual(['I tried drawing.'],[e['text'] for e in export_day(self.path,'2026-10-27')['evidence']])
        start,end=day_window('2026-03-07')
        self.assertEqual(23*3600,(end-start).total_seconds())
        start,end=day_window('2026-10-31')
        self.assertEqual(25*3600,(end-start).total_seconds())

    def test_identity_stable_revision_and_snapshot_change_on_meaning_change(self):
        row=self.add()
        first=export_day(self.path,'2026-10-27')
        self.store.connection.execute("UPDATE messages SET text='I started learning SQL.' WHERE message_key=?",(row['message_key'],))
        second=export_day(self.path,'2026-10-27')
        self.assertEqual(first['evidence'][0]['id'],second['evidence'][0]['id'])
        self.assertNotEqual(first['evidence'][0]['revision'],second['evidence'][0]['revision'])
        self.assertNotEqual(first['sourceFingerprint'],second['sourceFingerprint'])
        self.store.connection.execute("UPDATE messages SET source_time='2026-10-27T15:00:00Z' WHERE message_key=?",(row['message_key'],))
        self.assertNotEqual(second['evidence'][0]['revision'],export_day(self.path,'2026-10-27')['evidence'][0]['revision'])

    def test_bounds_fair_pages_and_oversize_are_reported_without_truncation(self):
        for i in range(4):self.add('I wanted to learn SQL '+str(i))
        self.add('I listened to music.',source='chatgpt-native',provenance=dict(adapter='chatgpt-native-v1',origin='human',timestamp_valid=True,user_only=True))
        first=export_day(self.path,'2026-10-27',max_items=2)
        self.assertEqual(2,len({e['sourceKind'] for e in first['evidence']}))
        self.assertEqual(3,first['coverage']['omitted'])
        second=export_day(self.path,'2026-10-27',offset=first['nextOffset'],expected_snapshot=first['sourceFingerprint'])
        self.assertEqual(5,len({e['id'] for e in first['evidence']+second['evidence']}))
        long=self.add('I was annoyed. '+'Still annoyed. '*500)
        small=export_day(self.path,'2026-10-27',max_bytes=800)
        self.assertGreater(small['coverage']['oversize'],0)
        self.assertNotIn(long['message_key'],[e['id'] for e in small['evidence']])
        self.assertTrue(all(e['text'] in ('I wanted to learn SQL '+str(i) for i in range(4)) or e['text']=='I listened to music.' for e in small['evidence']))
        with self.assertRaisesRegex(ValueError,'snapshot_changed'):
            export_day(self.path,'2026-10-27',offset=2,expected_snapshot=first['sourceFingerprint'])

    def test_readonly_and_excluded_conversation(self):
        self.add()
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        result=export_day(self.path,'2026-10-27',excluded_conversations={'private-chat'})
        self.assertEqual([],result['evidence'])
        self.assertEqual(before,hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(1,self.store.connection.execute('SELECT count(*) FROM messages').fetchone()[0])

    def test_work_profile_only_exports_local_verified_human_messages(self):
        self.add('I tried testing the workflow.')
        self.add('I listened to music.',source='chatgpt-native',provenance=dict(adapter='chatgpt-native-v1',origin='human',timestamp_valid=True,user_only=True))
        result=export_day(self.path,'2026-10-27',profile='work')
        self.assertEqual('work',result['profile'])
        self.assertEqual(['codex-local'],[e['sourceKind'] for e in result['evidence']])

    def test_cli_keeps_bodies_in_canonical_private_output_and_checks_timezone_hash(self):
        import contextlib
        import io
        import zoneinfo
        from export_narrative_evidence import main
        old_path=zoneinfo.TZPATH
        self.addCleanup(lambda:(reset_tzpath(old_path),ZoneInfo.clear_cache()))
        self.add('I was frustrated by the lyrics covering the album artwork.')
        zone_source=next(Path(p)/'America'/'Chicago' for p in zoneinfo.TZPATH
                         if (Path(p)/'America'/'Chicago').is_file())
        target=Path(self.tmp.name)/'tzdata'/'America'/'Chicago'
        target.parent.mkdir(parents=True);target.write_bytes(zone_source.read_bytes())
        settings=dict(profile='personal',data_dir=self.tmp.name,source_db=str(self.path),
                      queue_db=str(Path(self.tmp.name)/'personal-summary.sqlite3'),
                      gh_path=str(Path(self.tmp.name)/'gh.exe'),enabled=False,
                      repository='snackdriven/day-crumbs',branch='main',
                      tzdata_dir=str(target.parent.parent),tzdata_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        config=Path(self.tmp.name)/'personal-summary.json'
        config.write_text(json.dumps(settings),encoding='utf-8')
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            code=main(['--config',str(config),'--profile','personal','--day','2026-10-27'])
        self.assertEqual(0,code)
        metadata=json.loads(output.getvalue())
        self.assertNotIn('frustrated',output.getvalue())
        self.assertNotIn(self.tmp.name,output.getvalue())
        saved=Path(self.tmp.name)/'narrative-evidence'/'personal'/metadata['file']
        self.assertEqual(1,len(json.loads(saved.read_text(encoding='utf-8'))['evidence']))
        settings['tzdata_sha256']='0'*64;config.write_text(json.dumps(settings),encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(1,main(['--config',str(config),'--profile','personal','--day','2026-10-27']))


if __name__=='__main__':unittest.main()
