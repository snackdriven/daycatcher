import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import summary_service
from journal_collector.runtime import worker_lock


class Queue:
    items = []
    acknowledgements = []
    failures = []
    attempts = []
    def __init__(self, *args): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def prepare(self, limit=500): return {'prepared': len(self.items)}
    def ready(self, limit=20): return self.items[:limit]
    def begin_publish(self,key): self.attempts.append(key); return True
    def delivered(self, key, receipt):
        self.acknowledgements.append(key)
        type(self).items=[item for item in self.items if item['fragment_id']!=key]
        return {'state':'delivered'}
    def failed(self, key, code): self.failures.append((key, code))
    def status(self): return {'queued': len(self.items), 'review_required': 0}


class Publisher:
    result = {'state': 'published'}
    def __init__(self, *args, **kwargs): pass
    def publish(self, item):
        if isinstance(self.result, Exception): raise self.result
        return self.result


class SummaryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.config = self.data / 'summary.json'
        self.settings = {'data_dir': str(self.data), 'source_db': str(self.data/'collector.sqlite3'),
            'queue_db': str(self.data/'work-summary.sqlite3'), 'gh_path': str(self.data/'gh'),
            'enabled': True, 'repository': 'snackdriven/day-crumbs', 'branch': 'main'}
        self.config.write_text(json.dumps(self.settings))
        Queue.items=[]; Queue.acknowledgements=[]; Queue.failures=[]; Queue.attempts=[]; Publisher.result={'state':'published'}
        for name, value in [('SummaryQueue', Queue), ('Publisher', Publisher)]:
            p = patch.object(summary_service, name, value); p.start(); self.addCleanup(p.stop)
    def run_service(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = summary_service.main(['--config',str(self.config),*extra])
        return code, out.getvalue()+err.getvalue()
    def test_disabled_or_stopped_never_constructs_queue(self):
        self.settings['enabled']=False; self.config.write_text(json.dumps(self.settings))
        with patch.object(summary_service,'SummaryQueue',side_effect=AssertionError('must not read')):
            self.assertEqual(self.run_service()[0],0)
        self.settings['enabled']=True; self.config.write_text(json.dumps(self.settings))
        (self.data/'summary-STOP').touch()
        with patch.object(summary_service,'SummaryQueue',side_effect=AssertionError('must not read')):
            self.assertEqual(self.run_service()[0],0)
    def test_ack_only_after_confirmed_delivery(self):
        Queue.items=[{'fragment_id':'a'*32,'day':'2026-10-27','facts':['topic_testing'],'coverage':'partial'}]
        self.assertEqual(self.run_service()[0],0)
        self.assertEqual(Queue.acknowledgements,['a'*32])
    def test_failure_queued_and_private_diagnostic(self):
        Queue.items=[{'fragment_id':'a'*32,'day':'2026-10-27','facts':['topic_testing'],'coverage':'partial'}]
        Publisher.result=RuntimeError('SECRET transcript body')
        code, output=self.run_service()
        self.assertEqual(code,1); self.assertNotIn('SECRET',output)
        self.assertEqual(Queue.acknowledgements,[])
        self.assertEqual(len(Queue.failures),1)
        self.assertNotIn('SECRET',(self.data/'summary-status.json').read_text())
    def test_preview_disabled_stages_without_publish(self):
        self.settings['enabled']=False; self.config.write_text(json.dumps(self.settings))
        Queue.items=[{'fragment_id':'a'*32,'day':'2026-10-27','facts':['topic_testing'],'coverage':'partial'}]
        with patch.object(summary_service,'Publisher',side_effect=AssertionError('must not publish')):
            code, output=self.run_service('--dry-run')
        self.assertEqual(code,0); self.assertIn('topic_testing',output); self.assertFalse(Queue.acknowledgements)
    def test_preview_rejects_arbitrary_strings(self):
        Queue.items=[{'fragment_id':'a'*32,'day':'2026-10-27','facts':['SECRET'],'coverage':'partial'}]
        code, output=self.run_service('--dry-run')
        self.assertEqual(code,1); self.assertNotIn('SECRET',output)
    def test_stop_between_fragments_prevents_second_publish(self):
        Queue.items=[{'fragment_id':key*32,'day':'2026-10-27','facts':['topic_testing'],'coverage':'partial'} for key in 'ab']
        def publish(_self,item):
            (self.data/'summary-STOP').touch(); return {'state':'published'}
        with patch.object(Publisher,'publish',publish): self.assertEqual(self.run_service()[0],0)
        self.assertEqual(Queue.acknowledgements,['a'*32])
    def test_duplicate_worker_does_not_clobber_health(self):
        path=self.data/'summary-status.json'; path.write_text('{"state":"running"}')
        with worker_lock(self.data/'.summary-worker'):
            code,_=self.run_service()
        self.assertEqual(code,0); self.assertEqual(json.loads(path.read_text()),{'state':'running'})
    def test_destination_cannot_change_through_configuration(self):
        self.settings['repository']='someone/public'; self.config.write_text(json.dumps(self.settings))
        self.assertEqual(self.run_service()[0],1)
    def test_source_cannot_escape_private_runtime(self):
        self.settings['source_db']=str(self.data.parent/'other.sqlite3')
        self.config.write_text(json.dumps(self.settings))
        with patch.object(summary_service,'SummaryQueue',side_effect=AssertionError('must not read')):
            self.assertEqual(self.run_service()[0],1)
    def test_unconfirmed_receipt_does_not_acknowledge(self):
        Queue.items=[{'fragment_id':'a'*32,'day':'2026-10-27','facts':['topic_testing'],'coverage':'partial'}]
        Publisher.result={'state':'accepted'}
        self.assertEqual(self.run_service()[0],1)
        self.assertFalse(Queue.acknowledgements)
    def test_cannot_increase_approved_bounds(self):
        for name,value in [('limit',501),('max_publish',21)]:
            settings=dict(self.settings); settings[name]=value
            self.config.write_text(json.dumps(settings))
            self.assertEqual(self.run_service()[0],1)
    def test_source_change_after_remote_success_surfaces_review(self):
        Queue.items=[{'fragment_id':'a'*32,'day':'2026-10-27','facts':['topic_testing'],'coverage':'partial'}]
        with patch.object(Queue,'delivered',return_value={'state':'review_required'}):
            code, output=self.run_service()
        self.assertEqual(code,0); self.assertEqual(json.loads(output)['state'],'review_required')
    def test_nested_queue_counts_are_observable(self):
        with patch.object(Queue,'status',return_value={'counts':{'ready':2,'review_required':1,'secret':'SECRET'}}):
            code, output=self.run_service()
        health=json.loads(output)
        self.assertEqual(code,0); self.assertEqual(health['queued'],2)
        self.assertEqual(health['state'],'review_required'); self.assertNotIn('SECRET',output)
    def test_attempt_is_persisted_before_network(self):
        Queue.items=[{'fragment_id':'a'*32,'day':'2026-10-27','facts':['topic_testing'],'coverage':'partial'}]
        def publish(_self,item):
            self.assertIn(item['fragment_id'],Queue.attempts)
            return {'state':'published'}
        with patch.object(Publisher,'publish',publish):
            self.assertEqual(self.run_service()[0],0)
    def test_invalidated_before_attempt_does_not_publish(self):
        Queue.items=[{'fragment_id':'a'*32,'day':'2026-10-27','facts':['topic_testing'],'coverage':'partial'}]
        with patch.object(Queue,'begin_publish',return_value=False), \
             patch.object(Publisher,'publish',side_effect=AssertionError('must not publish')):
            self.assertEqual(self.run_service()[0],0)
        self.assertFalse(Queue.acknowledgements)
