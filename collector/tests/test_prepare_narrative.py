import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zoneinfo

from journal_collector.store import Store
from journal_collector.narrative_publish import NarrativePublisher, effective_manifest
from journal_collector.work_language import render_description
from journal_collector.work_moments import validate_handoff
from prepare_narrative import prepare, main


WORK_TEXT=render_description('intent','check','test_environment','none','none')
WORK_ID='a'*32


class FakeGH:
    def __init__(self):self.files={};self.private=True;self.writes=0;self.reads=[]
    def repo(self):return {'full_name':'snackdriven/day-crumbs','private':self.private,'default_branch':'main','permissions':{'push':True}}
    def read(self,path):self.reads.append(path);return self.files.get(path)
    def write(self,path,content,sha):
        self.writes+=1
        self.files[path]={'content':content,'sha':hashlib.sha1(content).hexdigest()}
        return {'blob_sha':self.files[path]['sha'],'commit_sha':'a'*40}


class PrepareTests(unittest.TestCase):
    def setUp(self):
        try:zoneinfo.ZoneInfo('America/Chicago')
        except Exception:raise unittest.SkipTest('Supply installed IANA data via PYTHONTZPATH')
        self.old_zonepath=zoneinfo.TZPATH
        self.addCleanup(lambda:(zoneinfo.reset_tzpath(self.old_zonepath),zoneinfo.ZoneInfo.clear_cache()))
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.data=Path(self.tmp.name)
        source=next((Path(p)/'America'/'Chicago' for p in zoneinfo.TZPATH if (Path(p)/'America'/'Chicago').is_file()),None)
        if source is None:raise unittest.SkipTest('Supply installed timezone file')
        zone=self.data/'tzdata'/'America'/'Chicago';zone.parent.mkdir(parents=True);zone.write_bytes(source.read_bytes())
        self.config=self.data/'personal-summary.json'
        self.settings=dict(profile='personal',data_dir=str(self.data),source_db=str(self.data/'collector.sqlite3'),
            queue_db=str(self.data/'personal-summary.sqlite3'),gh_path=str(self.data/'gh.exe'),enabled=False,
            repository='snackdriven/day-crumbs',branch='main',tzdata_dir=str(self.data/'tzdata'),
            tzdata_sha256=hashlib.sha256(zone.read_bytes()).hexdigest())
        self.config.write_text(json.dumps(self.settings),encoding='utf-8')
        self.store=Store(self.data/'collector.sqlite3');self.addCleanup(self.store.close)
        item=dict(native_id='one',conversation_id='private-chat',role='user',text='I wanted to learn SQL.',
            source_time='2026-10-27T14:00:00Z',observed_at='2026-10-27T14:01:00Z',complete=True,
            provenance=dict(adapter='codex-local-jsonl-v1',origin='native-user-role',timestamp_valid=True))
        self.store.ingest_batch(dict(batch_id='one',source='codex-local',cursor_key='private-file',cursor={},
            coverage={'status':'partial'},messages=[item]))
        self.gh=FakeGH();self.entry_path='entries/2026/10/2026-10-27.md'
        self.gh.files[self.entry_path]={'content':b'# 2026-10-27\n\nMy original journal paragraph.\n','sha':'b'*40}

    def run_prepare(self,**kwargs):return prepare(self.config,'2026-10-27',transport=self.gh,**kwargs)
    def bundle(self,result):return json.loads(Path(result['input']).read_text(encoding='utf-8'))

    def test_private_source_readonly_and_exact_existing_prose_work_missing(self):
        before=(self.data/'collector.sqlite3').read_bytes()
        result=self.run_prepare()
        bundle=self.bundle(result)
        self.assertEqual('prepared',result['state']);self.assertEqual('missing',result['work_state'])
        self.assertEqual([],bundle['work_evidence'])
        self.assertEqual(self.gh.files[self.entry_path]['content'].decode(),bundle['existing_prose'])
        self.assertEqual('personal',bundle['personal_evidence'][0]['source'])
        self.assertEqual(before,(self.data/'collector.sqlite3').read_bytes());self.assertEqual(0,self.gh.writes)

    def test_pinned_privacy_check_before_output(self):
        self.gh.private=False
        with self.assertRaises(Exception):self.run_prepare()
        self.assertFalse((self.data/'narrative-writer'/'inputs'/'2026-10-27.json').exists())

    def test_work_handoff_validated_and_never_substituted_for_missing(self):
        path='imports/work-moments/v1/2026-10-27.json'
        wire={'version':1,'day':'2026-10-27','timezone':'America/Chicago','cutoff':'03:00',
              'source':'work','processing':'on-device','coverage':'partial',
              'moments':[{'id':WORK_ID,'text':WORK_TEXT,'time':'2026-10-27T15:00:00Z',
                          'kind':'intent','sanitized':True}]}
        self.gh.files[path]={'content':json.dumps(wire).encode(),'sha':'c'*40}
        calls=[]
        work={'id':WORK_ID,'day':'2026-10-27','source':'work','sanitized':True,'text':WORK_TEXT,
              'time':'2026-10-27T15:00:00Z','kind':'intent'}
        def validator(value,**kwargs):calls.append(value);return validate_handoff(value,**kwargs)
        result=self.run_prepare(work_validator=validator)
        self.assertEqual([wire],calls)
        self.assertEqual([work],self.bundle(result)['work_evidence'])
        self.assertEqual('ready',result['work_state'])
        self.gh.files.pop(path)
        calls.clear();self.assertEqual('missing',self.run_prepare(work_validator=validator)['work_state'])
        self.assertEqual([],calls)

    def test_stop_and_canonical_config(self):
        (self.data/'narrative-STOP').touch()
        with self.assertRaises(Exception):self.run_prepare()
        (self.data/'narrative-STOP').unlink()
        other=self.data/'copy.json';other.write_bytes(self.config.read_bytes())
        with self.assertRaises(Exception):prepare(other,'2026-10-27',transport=self.gh)

    def test_work_stop_skips_fetch_and_replaces_active_bad_work_input_with_personal_only(self):
        path='imports/work-moments/v1/2026-10-27.json'
        # Simulate a pre-policy local input cache; do not bless legacy freeform
        # through the current validator simply to set up this pause regression.
        first=self.run_prepare()
        work={'id':'work_1','day':'2026-10-27','source':'work','sanitized':True,
              'text':'Synthetic private label should stay out.','time':'2026-10-27T15:00:00Z','kind':'attempt'}
        cached=self.bundle(first);cached['work_evidence']=[work]
        Path(first['input']).write_text(json.dumps(cached),encoding='utf-8')
        manifest=json.loads(Path(first['manifest']).read_text());manifest['evidence'].append(work)
        Path(first['manifest']).write_text(json.dumps(manifest),encoding='utf-8')
        self.gh.files[path]={'content':b'{"legacy_freeform":"Synthetic private label should stay out."}','sha':'c'*40}
        self.assertEqual(1,len(self.bundle(first)['work_evidence']))
        (self.data/'work-handoff-STOP').touch();self.gh.reads.clear()
        result=self.run_prepare(work_validator=lambda *a,**kw:self.fail('paused validator must not run'))
        self.assertEqual('prepared',result['state']);self.assertEqual('paused',result['work_state'])
        self.assertNotIn(path,self.gh.reads)
        self.assertEqual([],self.bundle(result)['work_evidence'])
        self.assertEqual(1,result['personal_count'])
        self.assertNotIn('Synthetic private label',Path(result['input']).read_text())
        self.assertNotIn('Synthetic private label',Path(result['manifest']).read_text())
        self.assertEqual(0,self.gh.writes)

    def test_work_stop_preserves_verified_old_work_only_and_never_replays_pending_work(self):
        path='imports/work-moments/v1/2026-10-27.json'
        self.gh.files[path]={'content':b'{}','sha':'c'*40}
        work={'id':WORK_ID,'day':'2026-10-27','source':'work','sanitized':True,'text':WORK_TEXT,
              'time':'2026-10-27T15:00:00Z','kind':'intent'}
        first=self.run_prepare(work_validator=lambda *a,**kw:{'version':1,'day':'2026-10-27','evidence':[work]})
        manifest=json.loads(Path(first['manifest']).read_text())
        entry={'version':1,'day':'2026-10-27','paragraphs':[{'text':WORK_TEXT,'evidence_ids':[WORK_ID]}]}
        receipts=self.data/'narrative-writer'/'receipts.sqlite3'
        NarrativePublisher('gh',receipts,transport=self.gh).publish(entry,manifest)
        (self.data/'work-handoff-STOP').touch();self.gh.reads.clear()
        before=receipts.read_bytes()
        result=self.run_prepare()
        self.assertEqual('prepared',result['state']);self.assertEqual('paused',result['work_state'])
        self.assertEqual([],self.bundle(result)['work_evidence'])
        self.assertEqual(entry['paragraphs'],self.bundle(result)['old_owned_paragraphs'])
        self.assertEqual(before,receipts.read_bytes());self.assertNotIn(path,self.gh.reads)
        with contextlib.closing(sqlite3.connect(receipts)) as db,db:
            db.execute("UPDATE narrative_receipts SET state='pending',last_error='attempting'")
        pending_before=receipts.read_bytes();writes_before=self.gh.writes
        pending=self.run_prepare()
        self.assertEqual('review_required',pending['state']);self.assertEqual('paused',pending['work_state'])
        self.assertNotIn('entry',pending);self.assertEqual(pending_before,receipts.read_bytes())
        self.assertEqual(writes_before,self.gh.writes)

    def deliver(self):
        first=self.run_prepare()
        manifest=json.loads(Path(first['manifest']).read_text(encoding='utf-8'))
        entry={'version':1,'day':'2026-10-27','paragraphs':[{'text':'I wanted to learn SQL.',
                'evidence_ids':[manifest['evidence'][0]['id']]}]}
        receipts=self.data/'narrative-writer'/'receipts.sqlite3'
        NarrativePublisher('gh',receipts,transport=self.gh).publish(entry,manifest)
        return entry,receipts

    def test_owned_paragraphs_reused_only_when_remote_and_support_match(self):
        entry,receipts=self.deliver()
        before=receipts.read_bytes()
        second=self.run_prepare()
        self.assertEqual(entry['paragraphs'],self.bundle(second)['old_owned_paragraphs'])
        self.assertEqual(before,receipts.read_bytes())
        self.gh.files[self.entry_path]['content']=self.gh.files[self.entry_path]['content'].replace(b'I wanted',b'I really wanted')
        self.assertEqual('review_required',self.run_prepare()['state'])

    def test_prior_used_revision_changed_or_outside_page_requires_review(self):
        self.deliver()
        self.store.connection.execute("UPDATE messages SET text='I no longer want to learn SQL.'")
        self.assertEqual('review_required',self.run_prepare()['state'])

    def test_exact_legacy_manifest_can_seed_trusted_immutable_support(self):
        _,receipt=self.deliver()
        with contextlib.closing(sqlite3.connect(receipt)) as db,db:db.execute('DELETE FROM narrative_support')
        archive=self.data/'narrative-writer'/'inputs'/'2026-10-27-manifests'
        old=next(archive.glob('*.json'))
        legacy=self.data/'narrative-writer'/'2026-10-27-manifest.json'
        old.replace(legacy);archive.rmdir()
        result=self.run_prepare()
        self.assertEqual('prepared',result['state'])
        self.assertTrue(any(archive.glob('*.json')))
        self.assertEqual(1,len(self.bundle(result)['old_owned_paragraphs']))

    def test_frozen_bounded_pagination_collects_more_than_first_page(self):
        result=self.run_prepare(max_items=1)
        self.assertEqual(1,result['coverage']['pages'])
        for n in range(3):
            self.store.connection.execute('INSERT INTO messages SELECT message_key||?,source,native_id||?,conversation_id,role,text,source_time,observed_at,complete,revision,provenance FROM messages WHERE native_id=?',
                (str(n),str(n),'one'))
        result=self.run_prepare(max_items=1)
        self.assertEqual(4,result['personal_count'])
        self.assertEqual(4,result['coverage']['pages'])
        self.assertEqual(0,result['coverage']['omitted'])

    def test_missing_new_work_preserves_only_prior_receipt_verified_work_support(self):
        path='imports/work-moments/v1/2026-10-27.json'
        self.gh.files[path]={'content':b'{}','sha':'c'*40}
        work={'id':WORK_ID,'day':'2026-10-27','source':'work','sanitized':True,'text':WORK_TEXT,
              'time':'2026-10-27T15:00:00Z','kind':'intent'}
        first=self.run_prepare(work_validator=lambda *a,**kw:{'version':1,'day':'2026-10-27','evidence':[work]})
        manifest=json.loads(Path(first['manifest']).read_text(encoding='utf-8'))
        entry={'version':1,'day':'2026-10-27','paragraphs':[{'text':WORK_TEXT,'evidence_ids':[WORK_ID]}]}
        NarrativePublisher('gh',self.data/'narrative-writer'/'receipts.sqlite3',transport=self.gh).publish(entry,manifest)
        del self.gh.files[path]
        second=self.run_prepare()
        self.assertEqual('prepared',second['state']);self.assertEqual('missing',second['work_state'])
        self.assertEqual([],self.bundle(second)['work_evidence'])
        self.assertEqual(entry['paragraphs'],self.bundle(second)['old_owned_paragraphs'])
        support=json.loads(Path(second['manifest']).read_text(encoding='utf-8'))
        self.assertIn(work,support['evidence'])

    def test_unverified_legacy_baseline_cannot_seed_support(self):
        self.deliver()
        archive=self.data/'narrative-writer'/'inputs'/'2026-10-27-manifests'
        for old in archive.glob('*.json'):old.unlink()
        archive.rmdir()
        # Simulate the legacy receipt without the new support table's trusted row.
        receipt=self.data/'narrative-writer'/'receipts.sqlite3'
        with contextlib.closing(sqlite3.connect(receipt)) as db,db:db.execute('DELETE FROM narrative_support')
        (self.data/'narrative-writer'/'2026-10-27-manifest.json').write_text('{}',encoding='utf-8')
        self.assertEqual('review_required',self.run_prepare()['state'])

    def test_minimal_published_support_table_wins_over_full_export_snapshot(self):
        self.store.connection.execute('INSERT INTO messages SELECT message_key||?,source,native_id||?,conversation_id,role,text,source_time,observed_at,complete,revision,provenance FROM messages WHERE native_id=?',
            ('extra','extra','one'))
        entry,receipts=self.deliver()
        result=self.run_prepare()
        self.assertEqual('prepared',result['state'])
        self.assertEqual(entry['paragraphs'],self.bundle(result)['old_owned_paragraphs'])

    def test_observation_advance_freezes_exact_old_used_support_and_digest(self):
        entry,receipts=self.deliver()
        with contextlib.closing(sqlite3.connect(receipts)) as db,db:
            digest,stored=db.execute('SELECT input_digest,manifest_json FROM narrative_support').fetchone()
        original=json.loads(stored)
        self.store.connection.execute("UPDATE messages SET observed_at='2026-10-27T17:00:00Z'")
        second=self.run_prepare()
        self.assertEqual('prepared',second['state'])
        current=json.loads(Path(second['manifest']).read_text(encoding='utf-8'))
        used=effective_manifest(entry,current)
        self.assertEqual(original,used)
        from prepare_narrative import _digest
        self.assertEqual(digest,_digest({'entry':entry,'manifest':used}))

    def test_accepted_write_pending_receipt_emits_exact_frozen_replay(self):
        entry,receipts=self.deliver()
        with contextlib.closing(sqlite3.connect(receipts)) as db,db:
            db.execute("UPDATE narrative_receipts SET state='pending',last_error='attempting'")
            expected=json.loads(db.execute('SELECT manifest_json FROM narrative_support').fetchone()[0])
        before=self.gh.writes
        result=self.run_prepare()
        self.assertEqual('pending_publish',result['state'])
        self.assertEqual(entry,json.loads(Path(result['entry']).read_text(encoding='utf-8')))
        self.assertEqual(expected,json.loads(Path(result['manifest']).read_text(encoding='utf-8')))
        self.assertEqual(before,self.gh.writes)
        # The exact publisher can now reconcile the already accepted remote span.
        self.assertEqual('reconciled',NarrativePublisher('gh',receipts,transport=self.gh).publish(entry,expected)['state'])

    def test_pending_cli_exit_two_exposes_only_private_replay_paths(self):
        _,receipts=self.deliver()
        # Pausing work must not block reconciliation of a personal-only receipt.
        (self.data/'work-handoff-STOP').touch()
        with contextlib.closing(sqlite3.connect(receipts)) as db,db:
            db.execute("UPDATE narrative_receipts SET state='pending',last_error='attempting'")
        output=io.StringIO()
        with patch('prepare_narrative.Publisher',side_effect=lambda *a,**kw:__import__('journal_collector.summary_publish',fromlist=['Publisher']).Publisher('gh',transport=self.gh)),contextlib.redirect_stdout(output):
            code=main(['--config',str(self.config),'--day','2026-10-27'])
        self.assertEqual(2,code)
        self.assertEqual('pending_publish',json.loads(output.getvalue())['state'])
        self.assertNotIn('SQL',output.getvalue())

    def test_stdout_metadata_only(self):
        output=io.StringIO()
        with patch('prepare_narrative.Publisher',side_effect=lambda *a,**kw:__import__('journal_collector.summary_publish',fromlist=['Publisher']).Publisher('gh',transport=self.gh)),contextlib.redirect_stdout(output):
            self.assertEqual(0,main(['--config',str(self.config),'--day','2026-10-27']))
        self.assertNotIn('SQL',output.getvalue());self.assertNotIn('evidence_ids',output.getvalue())

    def revise(self,text,**changes):
        row=dict(self.store.connection.execute('SELECT * FROM messages WHERE native_id=?',('one',)).fetchone())
        item={key:row[key] for key in ('native_id','conversation_id','role','source_time','observed_at','text')}
        item.update(complete=True,provenance=json.loads(row['provenance']),text=text)
        item.update(changes)
        self.store.ingest_batch(dict(batch_id='revision-'+str(row['revision']+1),source=row['source'],
            cursor_key='private-file',cursor={},coverage={'status':'partial'},messages=[item]))

    def approve_edit(self):
        from prepare_narrative import revision_review
        identity=json.loads((self.data/'narrative-writer'/'inputs'/'2026-10-27-manifest.json').read_text())['evidence'][0]['id']
        proposal=revision_review(self.config,'2026-10-27',evidence_id=identity,transport=self.gh)
        self.assertEqual('revision_review_proposed',proposal['state'])
        # A proposal alone is never an approval.
        self.assertEqual('review_required',self.run_prepare()['state'])
        approved=revision_review(self.config,'2026-10-27',proposal_hash=proposal['proposal_hash'],transport=self.gh)
        self.assertEqual('revision_review_approved',approved['state'])
        return proposal,approved

    def test_exact_personal_revision_review_preserves_prose_and_immutable_receipts(self):
        entry,receipts=self.deliver()
        receipt_before=receipts.read_bytes();remote_before=self.gh.files[self.entry_path]['content']
        self.revise('I wanted to learn SQl.')
        self.assertEqual('review_required',self.run_prepare()['state'])
        self.approve_edit()
        result=self.run_prepare()
        self.assertEqual('prepared',result['state'])
        self.assertEqual(entry['paragraphs'],self.bundle(result)['old_owned_paragraphs'])
        self.assertEqual(entry['paragraphs'][0]['evidence_ids'],self.bundle(result)['preservation_only_evidence_ids'])
        self.assertEqual(receipt_before,receipts.read_bytes())
        self.assertEqual(remote_before,self.gh.files[self.entry_path]['content'])

    def test_review_does_not_cover_a_later_edit_or_exclusion(self):
        self.deliver();self.revise('I wanted to learn SQl.');self.approve_edit()
        self.revise('I do not want to learn SQL.')
        self.assertEqual('review_required',self.run_prepare()['state'])
        self.store.exclude(self.store.connection.execute('SELECT message_key FROM messages').fetchone()[0])
        self.assertEqual('review_required',self.run_prepare()['state'])

    def test_provenance_change_cannot_be_approved_as_text_only(self):
        from prepare_narrative import revision_review
        self.deliver()
        identity=json.loads((self.data/'narrative-writer'/'inputs'/'2026-10-27-manifest.json').read_text())['evidence'][0]['id']
        self.revise('I wanted to learn SQl.',provenance=dict(adapter='codex-local-jsonl-v1',origin='native-user-role',timestamp_valid=True,extra='changed'))
        with self.assertRaises(ValueError):revision_review(self.config,'2026-10-27',evidence_id=identity,transport=self.gh)
        self.assertEqual('review_required',self.run_prepare()['state'])

    def test_proposal_hash_and_unknown_shape_cannot_approve(self):
        from prepare_narrative import revision_review
        self.deliver();self.revise('I wanted to learn SQl.')
        identity=json.loads((self.data/'narrative-writer'/'inputs'/'2026-10-27-manifest.json').read_text())['evidence'][0]['id']
        proposal=revision_review(self.config,'2026-10-27',evidence_id=identity,transport=self.gh)
        with self.assertRaises(ValueError):revision_review(self.config,'2026-10-27',proposal_hash='0'*64,transport=self.gh)
        path=Path(proposal['proposal']);data=json.loads(path.read_text());data['ignore_all_revisions']=True
        path.write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaises(ValueError):revision_review(self.config,'2026-10-27',proposal_hash=proposal['proposal_hash'],transport=self.gh)
        self.assertEqual('review_required',self.run_prepare()['state'])

    def test_approval_follows_verified_append_ancestry_only_for_unchanged_paragraphs(self):
        entry,receipts=self.deliver();self.revise('I wanted to learn SQl.');self.approve_edit()
        self.store.connection.execute('INSERT INTO messages SELECT message_key||?,source,native_id||?,conversation_id,role,?,source_time,observed_at,complete,revision,provenance FROM messages WHERE native_id=?',
            ('new','new','I wanted to paint.','one'))
        prepared=self.run_prepare()
        self.assertEqual('prepared',prepared['state'])
        manifest=json.loads(Path(prepared['manifest']).read_text())
        old_ids={i for p in entry['paragraphs'] for i in p['evidence_ids']}
        fresh=next(i for i in manifest['evidence'] if i['id'] not in old_ids)
        updated={**entry,'paragraphs':entry['paragraphs']+[{'text':'I wanted to paint.','evidence_ids':[fresh['id']]}]}
        NarrativePublisher('gh',receipts,transport=self.gh).publish(updated,manifest)
        result=self.run_prepare()
        self.assertEqual('prepared',result['state'])
        self.assertEqual(updated['paragraphs'],self.bundle(result)['old_owned_paragraphs'])

    def test_stale_proposal_and_later_provenance_change_do_not_reuse_review(self):
        from prepare_narrative import revision_review
        self.deliver();self.revise('I wanted to learn SQl.')
        identity=json.loads((self.data/'narrative-writer'/'inputs'/'2026-10-27-manifest.json').read_text())['evidence'][0]['id']
        proposal=revision_review(self.config,'2026-10-27',evidence_id=identity,transport=self.gh)
        self.revise('I wanted to learn SQl!')
        with self.assertRaises(ValueError):revision_review(self.config,'2026-10-27',proposal_hash=proposal['proposal_hash'],transport=self.gh)
        self.approve_edit()
        p=json.loads(self.store.connection.execute('SELECT provenance FROM messages WHERE native_id=?',('one',)).fetchone()[0]);p['new_attribution']='different'
        self.store.connection.execute('UPDATE messages SET provenance=? WHERE native_id=?',(json.dumps(p),'one'))
        self.assertEqual('review_required',self.run_prepare()['state'])


if __name__=='__main__':unittest.main()
