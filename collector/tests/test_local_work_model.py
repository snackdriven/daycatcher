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
             'evidence_ids':['m1','m2','m3','m4','m5']} for n in range(5)]},
            {'reviews':[{'candidate':f'c{n+1}','privacy_safe':False,'grounded':False} for n in range(5)]}])
        result=process_export(value,client)
        review=client.chat.call_args_list[1].args[1]
        self.assertEqual(set(review),{'messages','candidates'})
        self.assertEqual(len(review['messages']),5)
        for n in range(1,6):self.assertEqual(json.dumps(review).count(f'RAW_UNIQUE_{n}'),1)
        self.assertTrue(all(item['evidence_ids']==['m1','m2','m3','m4','m5'] for item in review['candidates']))
        self.assertEqual(result['withheld'],5)
    def test_utf8_payload_bounds_abort_without_truncation_before_local_call(self):
        value=export();value['evidence'][0]['text']='界'*9000
        client=Client()
        with self.assertRaises(LocalModelError) as error:process_export(value,client)
        self.assertEqual(error.exception.code,'local_model_batch_bounds');self.assertEqual(client.calls,[])
        value=export();value['evidence'][0]['text']='é'*11000
        client.chat=Mock(return_value={'moments':[
            {'text':'界'*600,'kind':'discussion','evidence_ids':['m1']} for _ in range(5)]})
        with self.assertRaises(LocalModelError) as error:process_export(value,client)
        self.assertEqual(error.exception.code,'local_model_batch_bounds');self.assertEqual(client.chat.call_count,1)
