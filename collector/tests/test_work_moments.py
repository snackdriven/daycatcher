import copy
import json
import unittest

from journal_collector.narrative_evidence import day_window
from journal_collector.summary_publish import PublishError
from journal_collector.work_moments import (
    WorkMomentError, WorkMomentPublisher, build_handoff, review_digest, validate_handoff,
)


DAY='2026-10-27'


def evidence():
    since,until=day_window(DAY)
    return {'schemaVersion':1,'profile':'work','day':DAY,'timezone':'America/Chicago','cutoff':'03:00',
            'window':{'since':since.isoformat(),'until':until.isoformat()},
            'evidence':[{'id':'a'*64,'revision':'b'*64,'sourceKind':'claude-code-local',
                         'time':'2026-10-27T14:00:00Z','observedAt':'2026-10-27T14:01:00Z',
                         'text':'PRIVATE_SOURCE_SENTINEL I asked who would cover testing.'}]}


def candidate(text='I asked who would cover testing.',kind='reported_action',ids=None):
    return {'text':text,'kind':kind,'evidence_ids':ids or ['a'*64]}


def reviewed(day,candidates,export):
    return {'processing':'on-device','privacy_reviewed':True,'grounding_reviewed':True,
            'approved':[review_digest(day,c,export) for c in candidates]}


class Remote:
    def __init__(self):
        self.content=None;self.sha='1'*40;self.writes=[];self.private=True
        self.conflict=False;self.ambiguous=False;self.drop=False
    def repo(self):
        return {'full_name':'snackdriven/day-crumbs','default_branch':'main',
                'private':self.private,'permissions':{'push':True}}
    def read(self,path):
        return None if self.content is None else {'content':self.content,'sha':self.sha}
    def write(self,path,content,sha):
        self.writes.append((path,sha))
        if self.conflict:
            self.conflict=False;raise PublishError('conflict')
        if sha!=(self.sha if self.content is not None else None):raise PublishError('conflict')
        if not self.drop:self.content=content;self.sha='2'*40
        if self.ambiguous:raise PublishError('network')
        return {'commit_sha':'3'*40,'blob_sha':self.sha}


class WorkMomentsTests(unittest.TestCase):
    def setUp(self):
        self.export=evidence();self.candidate=candidate()
    def build(self,candidates=None,export=None,review=None):
        candidates=[self.candidate] if candidates is None else candidates
        export=self.export if export is None else export
        return build_handoff(DAY,candidates,export,
                             reviewed(DAY,candidates,export) if review is None else review)
    def test_build_receiver_minimal_manifest_and_no_raw_support(self):
        result=self.build()
        self.assertEqual(set(result),{'version','day','timezone','cutoff','source','processing','coverage','moments'})
        self.assertEqual(result['processing'],'on-device');self.assertEqual(result['coverage'],'partial')
        moment=result['moments'][0]
        self.assertEqual(moment['time'],self.export['evidence'][0]['time'])
        self.assertRegex(moment['id'],r'^[0-9a-f]{64}$')
        self.assertNotIn('a'*64,json.dumps(result));self.assertNotIn('PRIVATE_SOURCE_SENTINEL',json.dumps(result))
        manifest=validate_handoff(json.dumps(result).encode(),DAY)
        self.assertEqual(manifest,{'version':1,'day':DAY,'evidence':[
            dict(moment,day=DAY,source='work')]})
        self.assertEqual(validate_handoff(result,expected_day=DAY),manifest)
        with self.assertRaises(WorkMomentError):validate_handoff(result,DAY,expected_day='2026-10-26')
    def test_unknown_support_day_and_fake_model_metadata_fail_closed(self):
        for changed in (dict(self.candidate,evidence_ids=['c'*64]),
                        dict(self.candidate,time='2026-10-27T15:00:00Z'),
                        dict(self.candidate,id='d'*64),dict(self.candidate,source='work')):
            with self.subTest(changed=changed),self.assertRaises(WorkMomentError):self.build([changed])
        for key,value in [('profile','personal'),('day','2026-10-26'),('timezone','UTC'),('cutoff','00:00')]:
            with self.subTest(key=key),self.assertRaises(WorkMomentError):self.build(export=dict(self.export,**{key:value}))
        for time in ('2026-10-27T07:59:00Z','2026-10-28T08:00:00Z','2026-10-27T14:00:00','yesterday'):
            changed=copy.deepcopy(self.export);changed['evidence'][0]['time']=time
            with self.subTest(time=time),self.assertRaises(WorkMomentError):self.build(export=changed)
    def test_requires_review_of_exact_candidate_and_source_revision(self):
        approval=reviewed(DAY,[self.candidate],self.export)
        for field,value in [('processing','cloud'),('privacy_reviewed',False),('grounding_reviewed',False),('approved',[])]:
            with self.subTest(field=field),self.assertRaises(WorkMomentError):self.build(review=dict(approval,**{field:value}))
        with self.assertRaises(WorkMomentError):self.build([candidate('I finished testing.')],review=approval)
        changed=copy.deepcopy(self.export);changed['evidence'][0]['revision']='c'*64
        with self.assertRaises(WorkMomentError):self.build(export=changed,review=approval)
    def test_obvious_sensitive_patterns_rejected_without_revealing_input(self):
        texts=['Email person@example.com','See https://example.com','Look in C:\\Users\\someone',
               'Read /Users/someone/private','Fixed TEAM-123','Ticket #123','Number 12345678',
               'Code `thing()`','Two\nlines','Set {"client":"private"}','Zero\u200bwidth']
        for text in texts:
            with self.subTest(text=text):
                with self.assertRaises(WorkMomentError) as error:self.build([candidate(text)])
                self.assertNotIn(text,str(error.exception))
    def test_quoted_instruction_is_data_and_no_builder_invents_facts(self):
        text='A prompt told me to ignore previous instructions, which annoyed me.'
        result=self.build([candidate(text,kind='discussion')])
        self.assertEqual(result['moments'][0]['text'],text)
        self.assertEqual(result['moments'][0]['kind'],'discussion')
        self.assertEqual(self.build([])['moments'],[])
        with self.assertRaises(WorkMomentError):self.build([candidate(kind='completed')])
        approval=reviewed(DAY,[self.candidate],self.export)
        # Mere evidence membership cannot justify an invented completion.
        with self.assertRaises(WorkMomentError):self.build([candidate('I finished testing.')],review=approval)
    def test_same_topic_distinct_evidence_keeps_distinct_moments_and_stable_ids(self):
        self.export['evidence'].append(dict(self.export['evidence'][0],id='e'*64,time='2026-10-27T16:00:00Z'))
        second=candidate('I asked who would cover testing.',ids=['e'*64])
        result=self.build([self.candidate,second])
        self.assertEqual(len({m['id'] for m in result['moments']}),2)
        revised=self.build([candidate('I asked about the testing responsibilities.')])
        self.assertEqual(result['moments'][0]['id'],revised['moments'][0]['id'])
    def test_receiver_rejects_bad_schema_duplicates_naive_time_and_day_mismatch(self):
        original=self.build()
        changes=[dict(original,source='personal'),dict(original,processing='cloud'),dict(original,version=True),
                 dict(original,secret='bad'),dict(original,day='2026-10-26'),
                 dict(original,moments=original['moments']*2)]
        for key,value in [('time','2026-10-27T14:00:00'),('sanitized',1),('kind','done'),('id','raw-id')]:
            moment=dict(original['moments'][0],**{key:value});changes.append(dict(original,moments=[moment]))
        for value in changes:
            with self.subTest(value=value),self.assertRaises(WorkMomentError):validate_handoff(json.dumps(value).encode(),DAY)
        duplicate=b'{"version":1,"version":1}'
        with self.assertRaises(WorkMomentError):validate_handoff(duplicate,DAY)
    def test_transport_pins_destination_and_reconciles_without_duplicate(self):
        remote=Remote();publisher=WorkMomentPublisher('gh',transport=remote)
        handoff=self.build();first=publisher.publish(handoff)
        self.assertEqual(first['state'],'published')
        self.assertEqual(remote.writes,[('imports/work-moments/v1/'+DAY+'.json',None)])
        self.assertEqual(publisher.publish(handoff)['state'],'reconciled');self.assertEqual(len(remote.writes),1)
        with self.assertRaises((WorkMomentError,PublishError)):WorkMomentPublisher('gh',repository='other/public',transport=remote)
        remote.private=False
        with self.assertRaises((WorkMomentError,PublishError)):publisher.publish(handoff)
    def test_transport_append_cas_and_changed_existing_id_requires_review(self):
        remote=Remote();publisher=WorkMomentPublisher('gh',transport=remote)
        publisher.publish(self.build());prior=remote.content
        changed=self.build([candidate('I asked about testing responsibilities.')])
        with self.assertRaises(WorkMomentError) as error:publisher.publish(changed)
        self.assertEqual(error.exception.code,'review_required');self.assertEqual(remote.content,prior)
        self.export['evidence'].append(dict(self.export['evidence'][0],id='e'*64))
        remote.conflict=True
        publisher.publish(self.build([candidate('I wanted less ambiguity.',kind='intent',ids=['e'*64])]))
        self.assertEqual(len(validate_handoff(remote.content,DAY)['evidence']),2)
        self.assertEqual(remote.writes[-1][1],'2'*40)
    def test_transport_ambiguous_success_readback_and_dropped_write(self):
        remote=Remote();remote.ambiguous=True
        publisher=WorkMomentPublisher('gh',transport=remote)
        self.assertEqual(publisher.publish(self.build())['state'],'reconciled')
        remote=Remote();remote.drop=True
        with self.assertRaises((WorkMomentError,PublishError)):WorkMomentPublisher('gh',transport=remote).publish(self.build())
