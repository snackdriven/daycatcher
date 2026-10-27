import copy
import contextlib
from datetime import datetime, timezone
import io
import json
import signal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import work_narrative_service as service
from journal_collector.runtime import worker_lock
from journal_collector.summary_publish import PublishError
from journal_collector.local_work_model import LocalStop
from journal_collector.narrative_evidence import day_window
from test_local_work_model import export, Client


class Context(Client):
    entered=0
    def __enter__(self):type(self).entered+=1;return self
    def __exit__(self,*args):self.exited=True


class WorkNarrativeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name);self.db=self.data/'collector.sqlite3';self.db.write_bytes(b'UNTOUCHED SOURCE')
        self.config=self.data/'collector.json'
        self.config.write_text(json.dumps({'data_dir':str(self.data),'db':str(self.db)}))
        self.settings=service.load_config(self.config)
        self.client=Context();self.factory=Mock(return_value=self.client)
        self.publisher=Mock();self.publisher.publish.return_value={'state':'published','blob_sha':'c'*40}
        self.publisher_factory=Mock(return_value=self.publisher)
        self.export=export();Context.entered=0
    def run_service(self,**kwargs):
        if kwargs.get('gh_path')=='/synthetic/gh':kwargs['gh_path']=str(self.data/'gh')
        return service.execute(self.settings,model='model:local',ollama_path='/synthetic/ollama',
            day='2026-10-27',client_factory=self.factory,publisher_factory=self.publisher_factory,
            exporter=lambda *args,**opts:copy.deepcopy(self.export),**kwargs)
    def test_default_preview_frozen_then_publish_without_regeneration_and_source_unchanged(self):
        result=self.run_service()
        self.assertEqual(result['state'],'preview');self.publisher_factory.assert_not_called()
        self.assertEqual(len(self.client.calls),2)
        result=self.run_service(publish=True,gh_path='/synthetic/gh')
        self.assertEqual(result['delivered'],1);self.assertEqual(len(self.client.calls),2)
        self.assertEqual(self.db.read_bytes(),b'UNTOUCHED SOURCE')
        ledger=json.loads((self.data/'work-narrative'/'state.json').read_text())
        self.assertEqual(ledger['outbox'][0]['state'],'delivered')
    def test_publish_failure_retries_exact_frozen_payload_no_model_regeneration(self):
        captured=[]
        def fail(payload):
            captured.append(copy.deepcopy(payload));raise PublishError('network')
        self.publisher.publish.side_effect=fail
        first=self.run_service(publish=True,gh_path='/synthetic/gh')
        self.assertEqual(first['state'],'error');self.assertEqual(first['delivered'],0)
        calls=len(self.client.calls)
        self.publisher.publish.side_effect=lambda payload:captured.append(copy.deepcopy(payload)) or {'state':'reconciled','blob_sha':'d'*40}
        second=self.run_service(publish=True,gh_path='/synthetic/gh')
        self.assertEqual(second['delivered'],1);self.assertEqual(captured[0],captured[1])
        self.assertEqual(len(self.client.calls),calls)
    def test_changed_processed_revision_requires_review_without_inference_or_publish(self):
        self.run_service()
        self.export['evidence'][0]['revision']='e'*64
        self.publisher_factory.reset_mock()
        result=self.run_service(publish=True,gh_path='/synthetic/gh')
        self.assertEqual(result['state'],'review_required')
        self.assertEqual(len(self.client.calls),2);self.publisher_factory.assert_not_called()
    def test_stop_busy_and_empty_do_not_start_model_or_touch_existing_status(self):
        runtime=self.data/'work-narrative';runtime.mkdir(exist_ok=True)
        status=runtime/'status.json';status.write_text('{"state":"running"}')
        with worker_lock(runtime/'.worker'):
            self.assertEqual(self.run_service()['state'],'busy')
        self.assertEqual(json.loads(status.read_text()),{'state':'running'})
        (self.data/'work-narrative-STOP').touch()
        self.assertEqual(self.run_service()['state'],'stopped')
        (self.data/'work-narrative-STOP').unlink()
        self.export['evidence']=[]
        self.assertEqual(self.run_service()['state'],'idle')
        self.factory.assert_not_called()
    def test_rejected_review_stays_local_and_status_contains_no_source_or_text(self):
        self.client.privacy=False
        result=self.run_service(publish=True,gh_path='/synthetic/gh')
        self.assertEqual(result['state'],'idle');self.publisher_factory.assert_not_called()
        self.assertEqual(result['withheld'],1)
        self.assertEqual(result['withheld_privacy'],0)
        self.assertEqual(result['withheld_duplicate'],0)
        self.assertEqual(result['withheld_semantic'],1)
        self.assertNotIn('PRIVATE_WORK_NAME',json.dumps(result))
        self.assertNotIn('a'*64,json.dumps(result))
        self.assertNotIn('covering testing',json.dumps(result))
        calls=len(self.client.calls)
        self.run_service(publish=True,gh_path='/synthetic/gh')
        self.assertEqual(len(self.client.calls),calls)
        self.client.privacy=True
        self.export['evidence'].append(dict(self.export['evidence'][0],id='e'*64))
        next_result=self.run_service(publish=True,gh_path='/synthetic/gh')
        self.assertEqual(next_result['delivered'],1)
    def test_partial_withholding_publishes_safe_other_moment(self):
        self.export['evidence'].append(dict(self.export['evidence'][0],id='e'*64))
        self.client.chat=Mock(side_effect=[{'moments':[
            {'text':'I asked about testing.','kind':'reported_action','evidence_ids':['m1']},
            {'text':'I wanted a clearer plan.','kind':'intent','evidence_ids':['m2']}]},
            {'reviews':[{'candidate':'c1','privacy_safe':False,'grounded':True},
                        {'candidate':'c2','privacy_safe':True,'grounded':True}]}])
        result=self.run_service(publish=True,gh_path='/synthetic/gh')
        self.assertEqual(result['delivered'],1);self.assertEqual(result['withheld'],1)
        moments=self.publisher.publish.call_args.args[0]['moments']
        self.assertEqual([m['text'] for m in moments],['I wanted a clearer plan.'])
    def test_signal_unwinds_model_and_restores_handlers_and_frozen_retry(self):
        previous=signal.getsignal(signal.SIGTERM)
        self.client.chat=Mock(side_effect=lambda *args:service._termination(signal.SIGTERM,None))
        result=self.run_service()
        self.assertEqual(result['state'],'stopped');self.assertTrue(self.client.exited)
        self.assertEqual(signal.getsignal(signal.SIGTERM),previous)
        with worker_lock(self.data/'work-narrative'/'.worker'):pass
        self.client=Context();self.factory.return_value=self.client
        self.publisher.publish.side_effect=lambda *_:service._termination(signal.SIGTERM,None)
        result=self.run_service(publish=True,gh_path='/synthetic/gh')
        self.assertEqual(result['state'],'stopped')
        ledger=json.loads((self.data/'work-narrative'/'state.json').read_text())
        self.assertEqual(ledger['outbox'][0]['state'],'pending')
    def test_stop_guard_rechecks_before_each_transport_write(self):
        stop=self.data/'work-narrative-STOP';transport=Mock()
        def repo():stop.touch();return {'private':True}
        transport.repo.side_effect=repo
        guarded=service.StopGuardedTransport(transport,stop)
        guarded.repo()
        with self.assertRaises(LocalStop):guarded.write('private',b'safe',None)
        transport.write.assert_not_called()
    def test_cli_refuses_non_macos_before_config_model_or_github(self):
        output=io.StringIO()
        with patch.object(service.sys,'platform','win32'), \
             patch.object(service,'load_config') as config,patch.object(service,'execute') as execute, \
             patch.object(service,'verify_published') as verify,contextlib.redirect_stdout(output):
            self.assertEqual(service.main([]),1)
        self.assertEqual(json.loads(output.getvalue())['error_code'],'work_requires_macos')
        config.assert_not_called();execute.assert_not_called();verify.assert_not_called()
    def test_malformed_model_output_reports_fixed_stage_reason_only(self):
        self.client.chat=Mock(return_value={'moments':[{'text':'PRIVATE_SECRET','kind':'discussion',
                                                       'evidence_ids':['hallucinated-id']}]})
        result=self.run_service()
        self.assertEqual(result['state'],'error')
        self.assertEqual(result['error_code'],'local_model_extraction_unknown_support')
        self.assertNotIn('PRIVATE_SECRET',json.dumps(result));self.assertNotIn('hallucinated-id',json.dumps(result))
    def test_legacy_fake_processor_missing_withholding_counters_defaults_zero(self):
        result=service.process_export(self.export,Client())
        keys=('withheld_privacy','withheld_duplicate','withheld_semantic')
        for key in keys:result.pop(key)
        with patch.object(service,'process_export',return_value=result):status=self.run_service()
        self.assertEqual(status['state'],'preview')
        self.assertTrue(all(status[key]==0 for key in keys))
    def test_more_than_two_batches_is_pending_and_max_five_per_batch(self):
        base=self.export['evidence'][0]
        self.export['evidence']=[dict(base,id=f'{n:064x}',revision=f'{n+100:064x}') for n in range(1,13)]
        result=self.run_service()
        self.assertEqual(result['processed'],10);self.assertEqual(result['pending'],2)
        self.assertEqual(len(self.client.calls),4)
        self.assertEqual([len(self.client.calls[n][1]['messages']) for n in (0,2)],[5,5])
    def test_default_days_process_current_then_one_previous_batch_without_backfill_starvation(self):
        today=copy.deepcopy(self.export);today['evidence'][0]['text']='CURRENT I asked about testing.'
        previous=copy.deepcopy(self.export);previous['day']='2026-10-26'
        since,until=day_window(previous['day'])
        previous['window']={'since':since.isoformat(),'until':until.isoformat()}
        base=previous['evidence'][0]
        previous['evidence']=[dict(base,id=f'{n:064x}',revision=f'{n+100:064x}',
            time='2026-10-26T14:00:00Z',observedAt='2026-10-26T14:01:00Z',
            text='PREVIOUS I asked about testing.') for n in range(1,17)]
        values={'2026-10-27':today,'2026-10-26':previous}
        result=service.execute(self.settings,model='model:local',ollama_path='/synthetic/ollama',
            client_factory=self.factory,publisher_factory=self.publisher_factory,
            exporter=lambda _db,selected,**_opts:copy.deepcopy(values[selected]),
            now=datetime(2026,10,27,17,tzinfo=timezone.utc))
        self.assertEqual(result['processed'],6);self.assertEqual(result['batches'],2)
        self.assertTrue(self.client.calls[0][1]['messages'][0]['text'].startswith('CURRENT'))
        self.assertTrue(self.client.calls[2][1]['messages'][0]['text'].startswith('PREVIOUS'))
        ledger=json.loads((self.data/'work-narrative'/'state.json').read_text())
        self.assertEqual([item['day'] for item in ledger['outbox']],['2026-10-27','2026-10-26'])
    def test_config_canonical_binding_and_missing_publish_gh_fail_closed(self):
        changed=dict(self.settings,db=str(self.data/'other.sqlite3'))
        self.config.write_text(json.dumps(changed))
        with self.assertRaises(ValueError):service.load_config(self.config)
        with self.assertRaises(ValueError):self.run_service(publish=True)
    def test_read_only_install_proof_checks_current_private_payload_while_stopped(self):
        self.run_service(publish=True,gh_path='/synthetic/gh')
        payload=self.publisher.publish.call_args.args[0]
        remote={'content':json.dumps(payload).encode(),'sha':'d'*40}
        self.publisher.publisher._read.return_value=remote
        (self.data/'work-narrative-STOP').touch()
        before=(self.data/'work-narrative'/'state.json').read_bytes()
        status_before=(self.data/'work-narrative'/'status.json').read_bytes()
        proof=service.verify_published(self.settings,model='model:local',ollama_path='/synthetic/ollama',
            gh_path=str(self.data/'gh'),publisher_factory=self.publisher_factory)
        self.assertEqual(proof,{'state':'verified','day':'2026-10-27','moments':1,'blob_sha':'d'*40})
        self.assertEqual(before,(self.data/'work-narrative'/'state.json').read_bytes())
        self.assertEqual(status_before,(self.data/'work-narrative'/'status.json').read_bytes())
        self.assertEqual(len(self.client.calls),2)
        changed=copy.deepcopy(payload);changed['moments'][0]['text']='I completed testing.'
        self.publisher.publisher._read.return_value={'content':json.dumps(changed).encode(),'sha':'e'*40}
        with self.assertRaises(ValueError):service.verify_published(self.settings,model='model:local',
            ollama_path='/synthetic/ollama',gh_path=str(self.data/'gh'),publisher_factory=self.publisher_factory)
