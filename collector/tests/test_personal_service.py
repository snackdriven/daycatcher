import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zoneinfo

import personal_summary_service as service
from journal_collector.store import Store


class FakePublisher:
    items=[]
    profiles=[]
    def __init__(self,*args,**kwargs): self.profiles.append(kwargs.get('profile'))
    def publish(self,item):
        self.items.append(item)
        return {'state':'published','blob_sha':'b'*40}


class PersonalServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name).resolve()
        self.config=self.data/'personal-summary.json'
        available=[Path(p)/'America'/'Chicago' for p in zoneinfo.TZPATH]
        source=next((p for p in available if p.is_file()),None)
        if source is None: self.skipTest('Supply installed IANA data via PYTHONTZPATH')
        target=self.data/'tzdata'/'America'/'Chicago'; target.parent.mkdir(parents=True)
        target.write_bytes(source.read_bytes())
        self.old_tzpath=zoneinfo.TZPATH
        self.addCleanup(lambda:(zoneinfo.reset_tzpath(self.old_tzpath),zoneinfo.ZoneInfo.clear_cache()))
        self.settings={'profile':'personal','enabled':True,'data_dir':str(self.data),
            'source_db':str(self.data/'collector.sqlite3'),'queue_db':str(self.data/'personal-summary.sqlite3'),
            'gh_path':str(self.data/'gh.exe'),'tzdata_dir':str(self.data/'tzdata'),
            'tzdata_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
            'repository':'snackdriven/day-crumbs','branch':'main','limit':500,'max_publish':20}
        self.save()
        FakePublisher.items=[]; FakePublisher.profiles=[]
        self.publisher=patch.object(service,'Publisher',FakePublisher);self.publisher.start();self.addCleanup(self.publisher.stop)
    def save(self): self.config.write_text(json.dumps(self.settings))
    def run_service(self,*args):
        out=io.StringIO()
        with contextlib.redirect_stdout(out): code=service.main(['--config',str(self.config),*args])
        return code,out.getvalue()
    def seed(self):
        for source,provenance,text,key in [
            ('codex-local',{'timestamp_valid':True,'origin':'native-user-role'},'learn music and art at home PRIVATE_SOURCE_SENTINEL','a'),
            ('claude-chat:'+'a'*20,{'adapter':'claude-chat-ambient-v1'},'I am tired','b')]:
            with Store(self.settings['source_db']) as store:
                store.ingest_batch({'batch_id':key,'source':source,'cursor_key':'fixture','cursor':{},
                    'coverage':{'status':'complete'},'messages':[{'native_id':key,'conversation_id':'fixture',
                    'role':'user','text':text,'source_time':'2026-10-27T12:00:00Z',
                    'observed_at':'2026-10-27T12:01:00Z','complete':True,'provenance':provenance}]})
    def test_real_queue_accounts_and_codex_only_enum_egress_and_repeat(self):
        self.seed()
        code,output=self.run_service()
        self.assertEqual(code,0); self.assertEqual(len(FakePublisher.items),1)
        facts=FakePublisher.items[0]['facts']
        self.assertIn('topic_home',facts);self.assertIn('topic_learning',facts);self.assertIn('reaction_tired',facts)
        self.assertEqual(FakePublisher.profiles,['personal'])
        self.assertNotIn('PRIVATE_SOURCE_SENTINEL',json.dumps(FakePublisher.items)+output)
        self.assertEqual(self.run_service()[0],0);self.assertEqual(len(FakePublisher.items),1)
        self.assertFalse((self.data/'work-summary.sqlite3').exists())
    def test_dry_run_does_not_publish_or_modify_source(self):
        self.seed(); before=Path(self.settings['source_db']).read_bytes()
        self.settings['enabled']=False;self.save()
        code,output=self.run_service('--dry-run')
        self.assertEqual(code,0);self.assertEqual(json.loads(output)['state'],'preview')
        self.assertFalse(FakePublisher.items);self.assertEqual(Path(self.settings['source_db']).read_bytes(),before)
    def test_timezone_integrity_fails_closed(self):
        self.seed(); self.settings['tzdata_sha256']='0'*64;self.save()
        code,output=self.run_service()
        self.assertEqual(code,1);self.assertFalse(FakePublisher.items);self.assertNotIn('PRIVATE_SOURCE_SENTINEL',output)
    def test_profile_destination_and_canonical_paths_fail_closed(self):
        for key,value in [('profile','work'),('repository','bad/public'),('queue_db',str(self.data/'work-summary.sqlite3'))]:
            changed=dict(self.settings);changed[key]=value;self.config.write_text(json.dumps(changed))
            self.assertEqual(self.run_service()[0],1)
        self.assertFalse(FakePublisher.items)
    def test_stop_is_separate_from_collector_and_work(self):
        self.seed();(self.data/'personal-summary-STOP').touch()
        code,output=self.run_service();self.assertEqual(code,0);self.assertEqual(json.loads(output)['state'],'stopped')
        self.assertFalse(FakePublisher.items);self.assertFalse((self.data/'STOP').exists())
        self.assertFalse((self.data/'summary-STOP').exists())
    def test_pythonw_absent_streams_get_metadata_log(self):
        self.settings['enabled']=False;self.save()
        with patch.object(sys,'stdout',None),patch.object(sys,'stderr',None):
            self.assertEqual(service.main(['--config',str(self.config)]),0)
        self.assertIn('disabled',(self.data/'personal-summary-worker.log').read_text())
