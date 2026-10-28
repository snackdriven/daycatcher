import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone

import work_narrative_service as narrative
import work_history_service as history
from journal_collector.runtime import worker_lock
from journal_collector.work_history import HistoryQueue
from test_local_work_model import export
from test_work_narrative_service import Context


class HistoryQueueTests(unittest.TestCase):
    def test_rotation_settlement_and_new_evidence_reopens(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'queue.json'
            queue=HistoryQueue(path)
            queue.discover({'2020-01-01':'a','2020-01-02':'b'})
            self.assertEqual(queue.next_day(), '2020-01-01')
            queue.ack('2020-01-01',{'state':'pending'})
            self.assertEqual(HistoryQueue(path).next_day(),'2020-01-02')
            queue.ack('2020-01-02',{'state':'idle','oversize_omitted':2})
            self.assertEqual(queue.summary()['settled_with_gaps'],1)
            queue.discover({'2020-01-02':'c'})
            self.assertEqual(queue.summary()['pending'],2)

    def test_removed_day_reopens_for_exclusion_review(self):
        with tempfile.TemporaryDirectory() as folder:
            queue=HistoryQueue(Path(folder)/'queue.json');queue.discover({'2020-01-01':'a'})
            queue.ack('2020-01-01',{'state':'idle'});queue.discover({})
            self.assertEqual(queue.next_day(),'2020-01-01')


class CoordinatorTests(unittest.TestCase):
    def test_live_priority_then_bounded_older_rotation_and_metadata_status(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder);config={'data_dir':str(data),'db':str(data/'db')}
            calls=[]
            def worker(config,**opts):calls.append(opts['day']);return {'state':'idle','delivered':1}
            dates={'2020-01-01':'a','2020-01-02':'b'}
            with patch.object(history,'load_history_config',return_value=config):
                result=history.run_once(config,model='model',ollama_path='/ollama',gh_path='/gh',
                    now=datetime(2026,10,27,20,tzinfo=timezone.utc),collector=lambda c:{'status':'partial'},
                    worker=worker,discover=lambda db:dates)
            self.assertEqual(calls,['2026-10-27','2026-10-26','2020-01-01'])
            self.assertEqual(result['queue']['settled'],1);self.assertEqual(result['passes'],3)
            self.assertNotIn('2020-01-01',json.dumps(result))
            queue=HistoryQueue(data/'work-history'/'queue.json')
            self.assertEqual(queue.next_day(),'2020-01-02')

    def test_stop_and_busy_never_acquire_or_clear_live_collector_control(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder);config={'data_dir':str(data),'db':str(data/'db')}
            (data/'STOP').write_text('preserve');(data/'work-history-STOP').touch()
            collector=Mock()
            result=history.run_once(config,model='model',ollama_path='/ollama',gh_path='/gh',collector=collector)
            self.assertEqual(result['state'],'stopped');collector.assert_not_called()
            (data/'work-history-STOP').unlink()
            with worker_lock(data/'work-history'/'.worker'):
                result=history.run_once(config,model='model',ollama_path='/ollama',gh_path='/gh',collector=collector)
            self.assertEqual(result['state'],'busy');collector.assert_not_called()
            self.assertEqual((data/'STOP').read_text(),'preserve')

    def test_live_busy_blocks_historical_model_start(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder);config={'data_dir':str(data),'db':str(data/'db')};worker=Mock(return_value={'state':'busy'})
            with patch.object(history,'load_history_config',return_value=config):
                result=history.run_once(config,model='model',ollama_path='/ollama',gh_path='/gh',
                    collector=lambda c:{'status':'partial'},worker=worker,discover=lambda db:{'2020-01-01':'a'})
            self.assertEqual(result['state'],'busy');self.assertEqual(worker.call_count,1)


class HistoricalServiceTests(unittest.TestCase):
    def test_owned_ledger_growth_beyond_old_read_ceiling_remains_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'ledger.json'
            evidence={f'{number:064x}':{'day':'2020-01-01','revision':'a'*64,'state':'withheld'} for number in range(22000)}
            path.write_text(json.dumps({'version':1,'model':'model','ollama':'/ollama','evidence':evidence,'outbox':[]}))
            self.assertGreater(path.stat().st_size,4*1024*1024)
            self.assertEqual(len(narrative._load(path,'model','/ollama')['evidence']),22000)

    def test_preexisting_33rd_frozen_payload_settles_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder);value=export();config={'data_dir':str(data),'db':str(data/'db')}
            opts=dict(model='model',ollama_path='/ollama',day=value['day'],history=True,
                      client_factory=lambda *a:Context(),exporter=lambda *a,**k:copy.deepcopy(value))
            narrative.execute(config,**opts)
            path=data/'work-narrative'/narrative.STATE_NAME;ledger=json.loads(path.read_text())
            record=ledger['outbox'][0];template=record['payload']['moments'][0]
            record['payload']['moments']=[dict(template,id=f'{number:064x}') for number in range(32)]
            record['digest']=narrative._digest(record['payload']);record['id']=record['digest'];record['state']='delivered'
            extra=copy.deepcopy(record);extra['payload']['moments']=[dict(template,id='f'*64)]
            extra['digest']=narrative._digest(extra['payload']);extra['id']=extra['digest'];extra['state']='pending'
            ledger['outbox'].append(extra);path.write_text(json.dumps(ledger))
            before=copy.deepcopy(ledger['outbox']);publisher=Mock()
            result=narrative.execute(config,publish=True,gh_path=str(data/'gh'),publisher_factory=publisher,**opts)
            self.assertEqual(result['state'],'idle');self.assertEqual(result['capacity_omitted'],1)
            self.assertEqual(json.loads(path.read_text())['outbox'],before);publisher.assert_not_called()
            opts['history']=False
            again=narrative.execute(config,publish=True,gh_path=str(data/'gh'),publisher_factory=publisher,**opts)
            self.assertEqual(again['frozen_pending'],0);publisher.assert_not_called()

    def test_omission_checkpoint_survives_death_before_evidence_ledger_write(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder);value=export();value['sourceFingerprint']='a'*64
            value['evidence'][0]['text']='x'*(narrative.MAX_CHARS+1)
            config={'data_dir':str(data),'db':str(data/'db')}
            opts=dict(model='model',ollama_path='/ollama',day=value['day'],history=True,
                      exporter=lambda *a,**k:copy.deepcopy(value))
            original=narrative._write
            def crash(path,value):
                if path.name==narrative.STATE_NAME:raise OSError('synthetic crash')
                return original(path,value)
            with patch.object(narrative,'_write',side_effect=crash):
                self.assertEqual(narrative.execute(config,**opts)['state'],'error')
            resumed=narrative.execute(config,**opts)
            self.assertEqual(resumed['state'],'idle');self.assertEqual(resumed['oversize_omitted'],1)
            self.assertEqual(resumed['pending'],0)

    def test_export_level_oversize_is_explicit_settled_gap(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder);value=export();value['evidence']=[]
            value['coverage']={'eligible':2,'oversize':2,'omitted':2,'remaining':0}
            result=narrative.execute({'data_dir':str(data),'db':str(data/'db')},model='model',
                ollama_path='/ollama',day=value['day'],history=True,exporter=lambda *a,**k:copy.deepcopy(value))
            self.assertEqual(result['state'],'idle');self.assertEqual(result['oversize_omitted'],2)
            self.assertEqual(result['pending'],0)

    def test_snapshot_bound_pages_resume_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder);value=export();calls=[]
            def exporter(*args,**opts):
                calls.append(opts)
                page=copy.deepcopy(value);page['sourceFingerprint']='a'*64
                offset=opts.get('offset',0)
                page['nextOffset']=1 if offset==0 else None
                page['evidence'][0]['id']=('b' if offset==0 else 'c')*64
                return page
            opts=dict(model='model',ollama_path='/ollama',day=value['day'],history=True,
                      client_factory=lambda *a:Context(),exporter=exporter)
            first=narrative.execute({'data_dir':str(data),'db':str(data/'db')},**opts)
            self.assertEqual(first['pending'],1)
            second=narrative.execute({'data_dir':str(data),'db':str(data/'db')},**opts)
            self.assertEqual(second['pending'],0)
            self.assertTrue(any(call.get('offset')==1 and call.get('expected_snapshot')=='a'*64 for call in calls))
            ledger=json.loads((data/'work-narrative'/narrative.STATE_NAME).read_text())
            self.assertEqual(len(ledger['evidence']),2)

    def test_daily_capacity_settles_without_creating_33rd_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder);value=export()
            runtime=data/'work-narrative';runtime.mkdir()
            # Produce one real controlled frozen handoff through the ordinary path.
            config={'data_dir':str(data),'db':str(data/'db')}
            opts=dict(model='model',ollama_path='/ollama',day=value['day'],history=True,
                      client_factory=lambda *a:Context(),exporter=lambda *a,**k:copy.deepcopy(value))
            narrative.execute(config,**opts)
            path=runtime/narrative.STATE_NAME;ledger=json.loads(path.read_text())
            record=ledger['outbox'][0]
            template=record['payload']['moments'][0]
            record['payload']['moments']=[dict(template,id=f'{number:064x}') for number in range(32)]
            record['digest']=narrative._digest(record['payload']);record['id']=record['digest'];record['state']='delivered'
            path.write_text(json.dumps(ledger))
            value['evidence'][0]['id']='f'*64
            factory=Mock();opts['client_factory']=factory
            result=narrative.execute(config,**opts)
            self.assertEqual(result['state'],'idle');self.assertEqual(result['capacity_omitted'],1)
            self.assertEqual(result['pending'],0);factory.assert_not_called()
            self.assertEqual(len(json.loads(path.read_text())['outbox']),1)

    def test_oversize_settles_without_model_and_resume_is_idle(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder);value=export()
            value['evidence'][0]['text']='x'*(narrative.MAX_CHARS+1)
            factory=Mock()
            opts=dict(model='model',ollama_path='/ollama',day=value['day'],
                      history=True,client_factory=factory,exporter=lambda *a,**k:copy.deepcopy(value))
            first=narrative.execute({'data_dir':str(data),'db':str(data/'db')},**opts)
            self.assertEqual(first['state'],'idle')
            self.assertEqual(first['oversize_omitted'],1)
            self.assertEqual(first['pending'],0)
            second=narrative.execute({'data_dir':str(data),'db':str(data/'db')},**opts)
            self.assertEqual(second['pending'],0);factory.assert_not_called()

    def test_history_stop_does_not_stop_live_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder);(data/'work-history-STOP').touch()
            result=narrative.execute({'data_dir':str(data),'db':str(data/'db')},
                model='model',ollama_path='/ollama',day='2020-01-01',history=True)
            self.assertEqual(result['state'],'stopped')
            self.assertFalse((data/'work-narrative-STOP').exists())


if __name__=='__main__':unittest.main()
