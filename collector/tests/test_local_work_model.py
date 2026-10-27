import copy
import json
import shlex
import signal
import subprocess
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, MagicMock, patch

from journal_collector.narrative_evidence import day_window
from journal_collector.local_work_model import LocalModelError, LocalStop, OllamaSession, process_export
from journal_collector.work_moments import WorkMomentError, build_handoff


def export():
    start,end=day_window('2026-10-27')
    return {'schemaVersion':1,'profile':'work','day':'2026-10-27','timezone':'America/Chicago','cutoff':'03:00',
            'window':{'since':start.isoformat(),'until':end.isoformat()},
            'evidence':[{'id':'a'*64,'revision':'b'*64,'sourceKind':'claude-code-local',
                         'time':'2026-10-27T14:00:00Z','observedAt':'2026-10-27T14:01:00Z',
                         'text':'PRIVATE_WORK_NAME I asked who was covering testing.'}]}


class Client:
    def __init__(self,privacy=True,grounded=True):
        self.calls=[];self.privacy=privacy;self.grounded=grounded
    def chat(self,instructions,payload,schema):
        self.calls.append((instructions,payload,schema))
        if len(self.calls)%2:
            return {'moments':[{'text':'I asked who was covering testing.','kind':'reported_action','evidence_ids':['m1']}]}
        return {'reviews':[{'candidate':'c1','privacy_safe':self.privacy,'grounded':self.grounded}]}


class LocalWorkModelTests(unittest.TestCase):
    def test_separate_extraction_review_known_aliases_and_no_raw_outbound(self):
        client=Client();result=process_export(export(),client)
        self.assertEqual(len(client.calls),2)
        self.assertIn('PRIVATE_WORK_NAME',json.dumps(client.calls[0][1]))
        self.assertNotIn('a'*64,json.dumps(client.calls))
        self.assertNotIn('PRIVATE_WORK_NAME',json.dumps(result['handoff']))
        self.assertEqual(result['handoff']['processing'],'on-device')
        self.assertEqual(result['handoff']['moments'][0]['kind'],'reported_action')
    def test_privacy_or_grounding_rejection_produces_no_moment(self):
        for privacy,grounded in [(False,True),(True,False),(False,False)]:
            with self.subTest(privacy=privacy,grounded=grounded):
                result=process_export(export(),Client(privacy,grounded))
                self.assertEqual(result['handoff']['moments'],[])
                self.assertEqual(result['withheld'],1)
                self.assertEqual(result['withheld_ids'],['a'*64])
    def test_model_metadata_unknown_support_and_nonboolean_review_rejected(self):
        for item in [dict(text='I tested.',kind='reported_action',evidence_ids=['raw']),
                     dict(text='I tested.',kind='reported_action',evidence_ids=['m1'],time='fake'),
                     dict(text='I tested.',kind='completed',evidence_ids=['m1'])]:
            client=Client();client.chat=Mock(return_value={'moments':[item]})
            with self.subTest(item=item),self.assertRaises(LocalModelError):process_export(export(),client)
        with self.assertRaises(LocalModelError):process_export(export(),Client(privacy=1))
    def test_bounds_fail_before_model_and_empty_does_not_call(self):
        client=Client();changed=export();changed['evidence']*=6
        with self.assertRaises(LocalModelError):process_export(changed,client)
        changed=export();changed['evidence'][0]['text']='x'*12001
        with self.assertRaises(LocalModelError):process_export(changed,client)
        self.assertEqual(client.calls,[])
        changed=export();changed['evidence']=[]
        self.assertEqual(process_export(changed,client)['handoff']['moments'],[])
        self.assertEqual(client.calls,[])
    def test_own_server_cloud_disabled_local_weights_verified_and_terminated(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);blob=root/'blobs'/'sha256-fixture';blob.parent.mkdir();blob.write_bytes(b'GGUFfixture')
            executable=root/'ollama';executable.touch()
            process=Mock();process.poll.return_value=None
            def response(_self,endpoint,payload=None):
                if endpoint=='/api/tags':return {'models':[{'name':'model:local','digest':'a'*64}]}
                return {'modelfile':'FROM '+shlex.quote(blob.as_posix()),'capabilities':['completion','thinking']}
            with patch('journal_collector.local_work_model.subprocess.Popen',return_value=process) as spawn, \
                 patch.object(OllamaSession,'_request',response):
                with OllamaSession(str(executable),'model:local',models_dir=root) as session:
                    self.assertNotIn(':11434',session.base_url)
                    self.assertTrue(session.local_verified)
                    self.assertEqual(session.capabilities,frozenset({'completion','thinking'}))
                args,kwargs=spawn.call_args
                self.assertEqual(args[0],[str(executable),'serve'])
                self.assertEqual(kwargs['env']['OLLAMA_NO_CLOUD'],'1')
                self.assertEqual(kwargs['env']['OLLAMA_DEBUG'],'0')
                self.assertTrue(kwargs['env']['OLLAMA_HOST'].startswith('127.0.0.1:'))
                process.terminate.assert_called_once()
    def test_remote_or_unverified_model_refused_before_raw_prompt(self):
        for name in ['model:cloud','remote/model','https://server/model']:
            with self.subTest(name=name),self.assertRaises(LocalModelError):OllamaSession('/missing',name)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);executable=root/'ollama';executable.touch()
            process=Mock();process.poll.return_value=None
            for show in [{'remote_host':'remote','modelfile':'FROM /missing'},
                         {'modelfile':'FROM model:tag'}, {'modelfile':'FROM /missing/blob'},
                         {'modelfile':'FROM https://server/weights'}]:
                def response(_self,endpoint,payload=None):
                    return {'models':[{'name':'model:local','digest':'a'*64}]} if endpoint=='/api/tags' else show
                with self.subTest(show=show),patch('journal_collector.local_work_model.subprocess.Popen',return_value=process), \
                     patch.object(OllamaSession,'_request',response),self.assertRaises(LocalModelError):
                    with OllamaSession(str(executable),'model:local',models_dir=root):pass
    def test_http_guard_uses_literal_loopback_no_proxy_redirect_and_bounds_response(self):
        with tempfile.TemporaryDirectory() as temp:
            executable=Path(temp)/'ollama'
            session=OllamaSession(str(executable),'model:local',models_dir=Path(temp))
            session.process=Mock();session.process.poll.return_value=None
            session.base_url='http://127.0.0.1:59999';session.deadline=time.monotonic()+10
            session.opener=MagicMock()
            with self.assertRaises(LocalModelError):session._request('/api/chat',{'raw':'PRIVATE'})
            session.opener.open.assert_not_called()
            session.local_verified=True
            response=Mock();response.geturl.return_value=session.base_url+'/api/chat'
            response.read.return_value=b'x'*(128*1024+1)
            session.opener.open.return_value.__enter__.return_value=response
            with self.assertRaises(LocalModelError):session._request('/api/chat',{'raw':'PRIVATE'})
            request=session.opener.open.call_args.args[0]
            self.assertEqual(request.full_url,'http://127.0.0.1:59999/api/chat')
            self.assertLessEqual(session.opener.open.call_args.kwargs['timeout'],10)
            response.geturl.return_value='https://remote.example/api/chat'
            with self.assertRaises(LocalModelError):session._request('/api/chat',{'raw':'PRIVATE'})
    def test_stop_during_owned_server_startup_still_terminates_child(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);executable=root/'ollama';executable.touch()
            process=Mock();process.poll.return_value=None
            with patch('journal_collector.local_work_model.subprocess.Popen',return_value=process), \
                 patch.object(OllamaSession,'_request',side_effect=LocalStop()),self.assertRaises(LocalStop):
                with OllamaSession(str(executable),'model:local',models_dir=root):pass
            process.terminate.assert_called_once()
    def test_chat_disables_thinking_only_for_explicit_verified_capability(self):
        with tempfile.TemporaryDirectory() as temp:
            for capabilities in (frozenset({'completion','thinking'}),frozenset({'completion'}),frozenset()):
                session=OllamaSession(str(Path(temp)/'ollama'),'model:local',models_dir=Path(temp))
                session.local_verified=True;session.capabilities=capabilities
                session._request=Mock(return_value={'done':True,'message':{
                    'role':'assistant','content':'{"result":true}','thinking':'PRIVATE_LOCAL_THOUGHT'}})
                self.assertEqual(session.chat('instructions',{'text':'synthetic'},{'type':'object'}),{'result':True})
                payload=session._request.call_args.args[1]
                if 'thinking' in capabilities:self.assertIs(payload.get('think'),False)
                else:self.assertNotIn('think',payload)
                self.assertNotIn('PRIVATE_LOCAL_THOUGHT',json.dumps(payload))
    def test_posix_cleanup_signals_only_owned_process_group_and_escalates(self):
        with tempfile.TemporaryDirectory() as temp:
            session=OllamaSession(str(Path(temp)/'ollama'),'model:local',models_dir=Path(temp))
            process=Mock();process.pid=24680;process.poll.return_value=None
            process.wait.side_effect=[subprocess.TimeoutExpired('owned server',5),None]
            session.process=process;session._group_pid=24680
            with patch('journal_collector.local_work_model.os.killpg',create=True) as killpg:
                session._terminate()
            self.assertEqual(killpg.call_args_list,[unittest.mock.call(24680,signal.SIGTERM),
                                                  unittest.mock.call(24680,getattr(signal,'SIGKILL',9))])
            process.terminate.assert_not_called();process.kill.assert_not_called()
    def test_review_shared_support_includes_each_raw_message_once(self):
        value=export();base=value['evidence'][0]
        value['evidence']=[dict(base,id=f'{n:064x}',text=f'RAW_UNIQUE_{n} I asked about testing.') for n in range(1,6)]
        client=Client();client.chat=Mock(side_effect=[{'moments':[
            {'text':f'I discussed a testing question {n}.','kind':'discussion',
             'evidence_ids':[f'm{j+1}' for j in range(5) if j!=n]} for n in range(5)]},
            {'reviews':[{'candidate':f'c{n+1}','privacy_safe':False,'grounded':False} for n in range(5)]}])
        result=process_export(value,client)
        review=client.chat.call_args_list[1].args[1]
        self.assertEqual(set(review),{'messages','candidates'})
        self.assertEqual(len(review['messages']),5)
        for n in range(1,6):self.assertEqual(json.dumps(review).count(f'RAW_UNIQUE_{n}'),1)
        self.assertTrue(all(len(item['evidence_ids'])==4 for item in review['candidates']))
        self.assertEqual(result['withheld'],5)
    def test_utf8_payload_bounds_abort_without_truncation_before_local_call(self):
        value=export();value['evidence'][0]['text']='界'*9000
        client=Client()
        with self.assertRaises(LocalModelError) as error:process_export(value,client)
        self.assertEqual(error.exception.code,'local_model_batch_bounds');self.assertEqual(client.calls,[])
        value=export();base=value['evidence'][0]
        value['evidence']=[dict(base,id=f'{n:064x}',text='é'*10900 if n==1 else 'I asked about testing.') for n in range(1,6)]
        client.chat=Mock(return_value={'moments':[
            {'text':'界'*600,'kind':'discussion','evidence_ids':[f'm{n+1}']} for n in range(5)]})
        with self.assertRaises(LocalModelError) as error:process_export(value,client)
        self.assertEqual(error.exception.code,'local_model_batch_bounds');self.assertEqual(client.chat.call_count,1)
    def test_dynamic_schemas_constrain_supported_aliases_and_exact_review_ids_count(self):
        client=Client();process_export(export(),client)
        extract_schema=client.calls[0][2]
        aliases=extract_schema['properties']['moments']['items']['properties']['evidence_ids']
        self.assertEqual(aliases['items']['enum'],['m1']);self.assertIs(aliases['uniqueItems'],True)
        reviews=client.calls[1][2]['properties']['reviews']
        self.assertEqual(reviews['minItems'],1);self.assertEqual(reviews['maxItems'],1)
        self.assertEqual(reviews['items']['properties']['candidate']['enum'],['c1'])
    def test_chat_grounds_owned_schema_in_system_prompt_and_bounds_entire_request(self):
        with tempfile.TemporaryDirectory() as temp:
            session=OllamaSession(str(Path(temp)/'ollama'),'model:local',models_dir=Path(temp))
            session.local_verified=True
            session._request=Mock(return_value={'done':True,'message':{'role':'assistant','content':'{}'}})
            schema={'type':'object','properties':{'result':{'type':'boolean'}},'required':['result']}
            payload={'text':'UNTRUSTED_SYNTHETIC_BODY'}
            session.chat('OWNED_INSTRUCTIONS',payload,schema)
            request=session._request.call_args.args[1]
            canonical=json.dumps(schema,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            self.assertTrue(request['messages'][0]['content'].startswith('OWNED_INSTRUCTIONS'))
            self.assertIn(canonical,request['messages'][0]['content'])
            self.assertNotIn('UNTRUSTED_SYNTHETIC_BODY',request['messages'][0]['content'])
            self.assertEqual(request['messages'][1]['role'],'user')
            self.assertEqual(json.loads(request['messages'][1]['content']),payload)
            session._request.reset_mock()
            with self.assertRaises(LocalModelError) as error:
                session.chat('OWNED_INSTRUCTIONS',{'text':'x'*(24*1024-100)},schema)
            self.assertEqual(error.exception.code,'local_model_batch_bounds')
            session._request.assert_not_called()
    def test_fixed_diagnostic_distinguishes_stage_reason_without_raw_output(self):
        malformed=[({'moments':[{'text':'PRIVATE_SECRET','kind':'discussion','evidence_ids':['hallucinated-id']}]},
                    None,'local_model_extraction_unknown_support'),
                   ({'moments':[{'text':'I asked about testing.','kind':'discussion','evidence_ids':['m1']}]},
                    {'reviews':[]},'local_model_review_count'),
                   ({'moments':[{'text':'I asked about testing.','kind':'discussion','evidence_ids':['m1']}]},
                    {'reviews':[{'candidate':'hallucinated-id','privacy_safe':True,'grounded':True}]},'local_model_review_unknown_candidate'),
                   ({'moments':[{'text':'I asked about testing.','kind':'discussion','evidence_ids':['m1']}]},
                    {'reviews':[{'candidate':'c1','privacy_safe':'PRIVATE_SECRET','grounded':True}]},'local_model_review_boolean')]
        for first,second,code in malformed:
            client=Client();client.chat=Mock(side_effect=[first,second])
            with self.subTest(code=code),self.assertRaises(LocalModelError) as error:process_export(export(),client)
            self.assertEqual(error.exception.code,code)
            self.assertNotIn('PRIVATE_SECRET',str(error.exception));self.assertNotIn('hallucinated-id',str(error.exception))
    def test_duplicate_episode_wordings_are_all_withheld_without_hiding_unique_candidate(self):
        value=export();value['evidence'].append(dict(value['evidence'][0],id='e'*64))
        client=Client();client.chat=Mock(side_effect=[{'moments':[
            {'text':'I discussed testing.','kind':'discussion','evidence_ids':['m1']},
            {'text':'I asked about testing.','kind':'reported_action','evidence_ids':['m1']},
            {'text':'I wanted clearer responsibilities.','kind':'intent','evidence_ids':['m2']}]},
            {'reviews':[{'candidate':'c1','privacy_safe':True,'grounded':True}]}])
        result=process_export(value,client)
        self.assertEqual(result['withheld'],2)
        self.assertEqual(result['withheld_ids'],['a'*64]);self.assertEqual(result['approved_ids'],['e'*64])
        self.assertEqual([m['text'] for m in result['handoff']['moments']],['I wanted clearer responsibilities.'])
        review=client.chat.call_args_list[1].args[1]
        self.assertEqual([c['evidence_ids'] for c in review['candidates']],[['m2']])
    def test_strict_privacy_rejection_withholds_exact_text_before_independent_review(self):
        value=export();value['evidence'].append(dict(value['evidence'][0],id='e'*64))
        for invalid in ('I asked\nabout testing.',' I asked about testing.','Read TEAM-123.'):
            client=Client();client.chat=Mock(side_effect=[{'moments':[
                {'text':invalid,'kind':'discussion','evidence_ids':['m1']},
                {'text':'I wanted clearer responsibilities.','kind':'intent','evidence_ids':['m2']}]},
                {'reviews':[{'candidate':'c1','privacy_safe':True,'grounded':True}]}])
            with self.subTest(invalid=invalid):
                result=process_export(value,client)
                self.assertEqual(result['withheld'],1);self.assertEqual(result['approved_ids'],['e'*64])
                review=client.chat.call_args_list[1].args[1]
                self.assertEqual([c['text'] for c in review['candidates']],['I wanted clearer responsibilities.'])
                self.assertNotIn(invalid,json.dumps(result['handoff']))
    def test_approved_support_prevails_over_withheld_overlap(self):
        value=export();value['evidence'].append(dict(value['evidence'][0],id='e'*64))
        client=Client();client.chat=Mock(side_effect=[{'moments':[
            {'text':'Read TEAM-123.','kind':'discussion','evidence_ids':['m1']},
            {'text':'I asked about testing responsibilities.','kind':'reported_action','evidence_ids':['m1','m2']}]},
            {'reviews':[{'candidate':'c1','privacy_safe':True,'grounded':True}]}])
        result=process_export(value,client)
        self.assertEqual(result['withheld'],1);self.assertEqual(result['withheld_ids'],[])
        self.assertEqual(result['approved_ids'],['a'*64,'e'*64]);self.assertEqual(len(result['handoff']['moments']),1)
    def test_all_duplicate_episodes_abstain_without_semantic_call_or_wording_choice(self):
        client=Client();client.chat=Mock(return_value={'moments':[
            {'text':text,'kind':'discussion','evidence_ids':['m1']} for text in ('I asked about testing.','I discussed testing.')]})
        result=process_export(export(),client)
        self.assertEqual(result['handoff']['moments'],[]);self.assertEqual(result['withheld'],2)
        self.assertEqual(client.chat.call_count,1)
    def test_fatal_export_and_unexpected_candidate_validation_have_fixed_codes(self):
        client=Client();value=export();value['profile']='personal'
        with self.assertRaises(LocalModelError) as error:process_export(value,client)
        self.assertEqual(error.exception.code,'local_model_handoff_invalid_evidence');self.assertEqual(client.calls,[])
        with patch('journal_collector.local_work_model.review_digest',side_effect=WorkMomentError('invalid_work_candidate')):
            with self.assertRaises(LocalModelError) as error:process_export(export(),Client())
        self.assertEqual(error.exception.code,'local_model_handoff_candidate_validation')
        value=export()
        empty=build_handoff(value['day'],[],value,{'processing':'on-device','privacy_reviewed':True,
                                               'grounding_reviewed':True,'approved':[]})
        with patch('journal_collector.local_work_model.build_handoff',side_effect=[empty,WorkMomentError()]):
            with self.assertRaises(LocalModelError) as error:process_export(value,Client())
        self.assertEqual(error.exception.code,'local_model_handoff_build')
    def test_withholding_counters_have_one_primary_reason_and_sum_to_total(self):
        value=export();base=value['evidence'][0]
        value['evidence']=[base,dict(base,id='e'*64),dict(base,id='f'*64)]
        client=Client();client.chat=Mock(side_effect=[{'moments':[
            {'text':'Read TEAM-123.','kind':'discussion','evidence_ids':['m1']},
            {'text':'I discussed testing.','kind':'discussion','evidence_ids':['m1']},
            {'text':'I wanted clearer responsibilities.','kind':'intent','evidence_ids':['m2']},
            {'text':'I asked about testing responsibilities.','kind':'reported_action','evidence_ids':['m3']}]},
            {'reviews':[{'candidate':'c1','privacy_safe':False,'grounded':True},
                        {'candidate':'c2','privacy_safe':True,'grounded':True}]}])
        result=process_export(value,client)
        self.assertEqual(result['withheld'],3)
        for key in ('withheld_privacy','withheld_duplicate','withheld_semantic'):
            self.assertEqual(result[key],1);self.assertIs(type(result[key]),int)
        self.assertEqual(result['withheld'],sum(result[k] for k in ('withheld_privacy','withheld_duplicate','withheld_semantic')))
        self.assertEqual(len(result['handoff']['moments']),1)
        self.assertNotIn('withheld_privacy',result['handoff'])
