"""Portable producer installer tests. Fake homes/launchctl only."""
import importlib.util
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch, call

spec=importlib.util.spec_from_file_location('narrative_install',Path(__file__).with_name('narrative_install.py'))
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name).resolve();self.p=installer.paths(self.home)
        self.p['data'].mkdir(parents=True);self.p['raw_code'].mkdir()
        self.p['raw_plist'].parent.mkdir(parents=True)
        self.config={'version':1,'data_dir':str(self.p['data']),'db':str(self.p['data']/'collector.sqlite3'),'poll_seconds':15,'local':{'since':'2026-10-26T00:00:00Z'}}
        self.p['config'].write_text(json.dumps(self.config),encoding='utf-8')
        self.raw_plist={'Label':installer.RAW_LABEL,'ProgramArguments':[sys.executable,'-m','journal_collector','watch','--config',str(self.p['config'])],'WorkingDirectory':str(self.p['raw_code']),'EnvironmentVariables':{'PYTHONPATH':str(self.p['raw_code']),'PYTHONUNBUFFERED':'1'}}
        self.p['raw_plist'].write_bytes(plistlib.dumps(self.raw_plist))
        self.ollama=self.home/'ollama';self.ollama.write_bytes(b'fixture')
        self.gh=self.home/'gh';self.gh.write_bytes(b'fixture')
        self.source=self.home/'bundle';(self.source/'journal_collector').mkdir(parents=True);(self.source/'macos').mkdir()
        for name in installer.REQUIRED_PACKAGE:
            (self.source/'journal_collector'/name).write_text('# fixture\n',encoding='utf-8')
        for name in installer.STANDALONE:
            (self.source/name).write_text('# fixture\n',encoding='utf-8')
        for name in ('install.py','narrative_install.py'):
            (self.source/'macos'/name).write_text('# fixture\n',encoding='utf-8')
        self.originals={self.p['config']:self.p['config'].read_bytes(),self.p['raw_plist']:self.p['raw_plist'].read_bytes()}
        for name in ('collector.sqlite3','work-summary.sqlite3','summary.json','summary-STOP'):
            path=self.p['data']/name;path.write_bytes(b'preserve exactly');self.originals[path]=path.read_bytes()
    def prepare(self):return installer.prepare(self.p,ollama=str(self.ollama),model='local-model',gh=str(self.gh),source=self.source)
    def legacy(self):
        self.prepare();value=json.loads(self.p['settings'].read_text(encoding='utf-8'))
        value['version']=1;value.pop('policy',None)
        value['files'].pop('journal_collector/work_language.py')
        (self.p['code']/'journal_collector'/'work_language.py').unlink()
        self.p['settings'].write_text(json.dumps(value),encoding='utf-8')
        self.p['plist'].write_bytes(plistlib.dumps(installer.plist_value(self.p,value)))
        return value
    def test_prepare_disabled_no_jobs_and_originals_unchanged(self):
        with patch.object(installer,'launchctl') as command:
            result=self.prepare()
        command.assert_not_called();self.assertFalse(result['enabled'])
        plist=plistlib.loads(self.p['plist'].read_bytes())
        self.assertTrue(plist['Disabled']);self.assertFalse(plist['KeepAlive']);self.assertTrue(plist['RunAtLoad']);self.assertEqual(3600,plist['StartInterval'])
        self.assertEqual(str(self.p['code']),plist['WorkingDirectory'])
        self.assertEqual(str(self.home),plist['EnvironmentVariables']['HOME'])
        self.assertEqual(str(self.p['code']),plist['EnvironmentVariables']['PYTHONPATH'])
        self.assertIn('--publish',plist['ProgramArguments']);self.assertTrue(self.p['stop'].exists())
        for path,original in self.originals.items():self.assertEqual(original,path.read_bytes())
    def test_requires_bound_raw_collector(self):
        self.raw_plist['ProgramArguments'][-1]=str(self.home/'elsewhere.json')
        self.p['raw_plist'].write_bytes(plistlib.dumps(self.raw_plist))
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse(self.p['plist'].exists())
    def test_complete_package_required_before_writes(self):
        (self.source/'journal_collector'/'local_work_model.py').unlink()
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse(self.p['code'].exists())
    def test_tampered_owned_plist_rejected_before_start_stop(self):
        self.prepare();value=plistlib.loads(self.p['plist'].read_bytes());value['ProgramArguments'][1]='/another/worker.py';self.p['plist'].write_bytes(plistlib.dumps(value))
        for operation in (installer.start,installer.stop):
            with self.subTest(operation=operation.__name__),patch.object(installer,'launchctl') as command:
                with self.assertRaises(ValueError):operation(self.p,'gui/501')
                command.assert_not_called()
        self.assertTrue(self.p['stop'].exists())
    def test_start_requires_raw_health_and_handoff_proof(self):
        self.prepare()
        with patch.object(installer,'raw_health',side_effect=ValueError('unhealthy')),patch.object(installer,'launchctl') as command:
            with self.assertRaises(ValueError):installer.start(self.p,'gui/501')
        command.assert_not_called();self.assertTrue(self.p['stop'].exists())
        with patch.object(installer,'raw_health',return_value={'healthy':True}),patch.object(installer,'verify_handoff',side_effect=ValueError('no proof')),patch.object(installer,'launchctl') as command:
            with self.assertRaises(ValueError):installer.start(self.p,'gui/501')
        command.assert_not_called();self.assertTrue(self.p['stop'].exists())
    def test_start_only_owned_job_after_verified_handoff(self):
        self.prepare()
        with patch.object(installer,'raw_health',return_value={'healthy':True,'backlog_bytes':1567000000}),patch.object(installer,'verify_handoff',return_value={'state':'verified','moments':2}),patch.object(installer,'loaded',side_effect=[False,True]),patch.object(installer,'launchctl') as command:
            result=installer.start(self.p,'gui/501')
        self.assertTrue(result['enabled']);self.assertFalse(self.p['stop'].exists())
        self.assertEqual([call('enable','gui/501/'+installer.LABEL),call('bootstrap','gui/501',str(self.p['plist']))],command.call_args_list)
        for path,original in self.originals.items():self.assertEqual(original,path.read_bytes())
    def test_bootstrap_failure_reinstates_stop_and_disabled_plist(self):
        self.prepare()
        with patch.object(installer,'raw_health',return_value={'healthy':True}),patch.object(installer,'verify_handoff',return_value={'state':'verified','moments':2}),patch.object(installer,'loaded',return_value=False),patch.object(installer,'launchctl',side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):installer.start(self.p,'gui/501')
        self.assertTrue(self.p['stop'].exists());self.assertTrue(plistlib.loads(self.p['plist'].read_bytes())['Disabled'])
    def test_stop_removes_only_new_job(self):
        self.prepare()
        with patch.object(installer,'loaded',side_effect=[True,False]),patch.object(installer,'launchctl') as command:
            self.assertTrue(installer.stop(self.p,'gui/501')['stopped'])
        self.assertEqual([call('disable','gui/501/'+installer.LABEL),call('bootout','gui/501/'+installer.LABEL)],command.call_args_list)
        for path,original in self.originals.items():self.assertEqual(original,path.read_bytes())
    def test_raw_health_accepts_fresh_partial_with_backlog(self):
        from datetime import datetime,timezone
        value={'state':'running','pid':123,'scans':2,'checked_at':datetime.now(timezone.utc).isoformat(),'scan':{'status':'partial','errors':[],'backlog_bytes':1567000000}}
        (self.p['data']/'local-status.json').write_text(json.dumps(value),encoding='utf-8')
        with patch.object(installer,'loaded',return_value=True),patch.object(installer,'raw_locked',return_value=True),patch.object(installer,'process_alive',return_value=True):
            result=installer.raw_health(self.p,'gui/501')
        self.assertTrue(result['healthy']);self.assertEqual(1567000000,result['backlog_bytes'])
    def test_live_home_override_rejected(self):
        with patch.object(installer.sys,'platform','darwin'),patch.object(installer,'launchctl') as command:
            self.assertEqual(1,installer.main(['start','--home',str(self.home)]))
        command.assert_not_called()
    def test_plan_is_default_and_writes_nothing(self):
        import contextlib,io
        output=io.StringIO()
        with patch.object(installer,'launchctl') as command,contextlib.redirect_stdout(output):
            self.assertEqual(0,installer.main(['--home',str(self.home)]))
        self.assertEqual('plan',json.loads(output.getvalue())['state'])
        self.assertFalse(self.p['settings'].exists());command.assert_not_called()
    def test_changed_installed_code_blocks_start_but_does_not_prevent_stop(self):
        self.prepare();(self.p['code']/'work_narrative_service.py').write_text('# changed\n',encoding='utf-8')
        with patch.object(installer,'launchctl') as command:
            with self.assertRaises(ValueError):installer.start(self.p,'gui/501')
        command.assert_not_called()
        with patch.object(installer,'loaded',return_value=False),patch.object(installer,'launchctl'):
            self.assertTrue(installer.stop(self.p,'gui/501')['stopped'])
    def test_stop_available_after_ollama_removed(self):
        self.prepare();self.ollama.unlink()
        with patch.object(installer,'loaded',return_value=False),patch.object(installer,'launchctl'):
            self.assertTrue(installer.stop(self.p,'gui/501')['stopped'])
    def test_readiness_uses_no_model_verification_only_and_metadata(self):
        import os
        from datetime import datetime,timezone
        from subprocess import CompletedProcess
        self.prepare();value=installer.settings(self.p)
        self.p['status'].parent.mkdir();self.p['status'].write_text('{}',encoding='utf-8')
        stamp=datetime.now(timezone.utc).timestamp()+1;os.utime(self.p['status'],(stamp,stamp))
        proof={'state':'verified','day':'2026-10-27','moments':2,'blob_sha':'a'*40,'body':'must not return'}
        with patch.object(installer.subprocess,'run',return_value=CompletedProcess([],0,json.dumps(proof),'')) as run:
            result=installer.verify_handoff(self.p,value)
        self.assertNotIn('body',result)
        command=run.call_args.args[0]
        self.assertIn('--verify-handoff-only',command);self.assertNotIn('--publish',command)
        self.assertEqual(str(self.p['code']),run.call_args.kwargs['env']['PYTHONPATH'])
    def test_readiness_requires_real_nonempty_private_blob(self):
        import os
        from datetime import datetime,timezone
        from subprocess import CompletedProcess
        self.prepare();value=installer.settings(self.p)
        self.p['status'].parent.mkdir();self.p['status'].write_text('{}',encoding='utf-8')
        stamp=datetime.now(timezone.utc).timestamp()+1;os.utime(self.p['status'],(stamp,stamp))
        for proof in ({'state':'preview','moments':2},{'state':'verified','moments':0,'blob_sha':'a'*40,'day':'2026-10-27'}):
            with self.subTest(proof=proof),patch.object(installer.subprocess,'run',return_value=CompletedProcess([],0,json.dumps(proof),'')):
                with self.assertRaises(ValueError):installer.verify_handoff(self.p,value)
    def test_raw_health_rejects_errors_stale_and_missing_lock(self):
        from datetime import datetime,timezone
        base={'state':'running','pid':123,'scans':2,'checked_at':datetime.now(timezone.utc).isoformat(),'scan':{'status':'partial','errors':[],'backlog_bytes':1567000000}}
        for variant in ({**base,'checked_at':'2026-01-01T00:00:00Z'},{**base,'scan':{'status':'error','errors':['failure']}},base):
            (self.p['data']/'local-status.json').write_text(json.dumps(variant),encoding='utf-8')
            with patch.object(installer,'loaded',return_value=True),patch.object(installer,'raw_locked',return_value=False),patch.object(installer,'process_alive',return_value=True):
                with self.assertRaises(ValueError):installer.raw_health(self.p,'gui/501')
    def test_actual_bundle_stages_and_imports_without_model_or_network(self):
        import subprocess
        source=Path(__file__).resolve().parents[1]
        installer.prepare(self.p,ollama=str(self.ollama),model='local-model',gh=str(self.gh),source=source)
        result=subprocess.run([sys.executable,'-c','import work_narrative_service; import export_narrative_evidence; print("imports-ok")'],cwd=self.p['code'],capture_output=True,text=True,timeout=15)
        self.assertEqual(0,result.returncode,result.stderr);self.assertEqual('imports-ok',result.stdout.strip())
        self.assertFalse((self.p['data']/'work-narrative'/'state.json').exists())
    def test_stop_waits_for_owned_worker_lock_after_job_unloads(self):
        self.prepare()
        with patch.object(installer,'loaded',return_value=False),patch.object(installer,'worker_locked',side_effect=[True,False]),patch.object(installer,'launchctl'),patch.object(installer.time,'sleep') as sleep:
            result=installer.stop(self.p,'gui/501')
        self.assertTrue(result['stopped']);self.assertEqual(1,sleep.call_count)
    def test_start_refuses_active_orphan_worker(self):
        self.prepare()
        with patch.object(installer,'raw_health',return_value={'healthy':True}),patch.object(installer,'verify_handoff',return_value={'state':'verified','moments':2}),patch.object(installer,'loaded',return_value=False),patch.object(installer,'worker_locked',return_value=True),patch.object(installer,'launchctl') as command:
            with self.assertRaises(ValueError):installer.start(self.p,'gui/501')
        command.assert_not_called();self.assertTrue(self.p['stop'].exists())
    def test_custom_model_root_is_pinned_in_settings_and_launchd(self):
        selected=self.home/'external weights'/'models'
        with patch.dict(installer.os.environ,{'OLLAMA_MODELS':str(selected)}):
            self.prepare()
        value=installer.settings(self.p)
        self.assertEqual(str(selected.resolve()),value['models_dir'])
        actual=plistlib.loads(self.p['plist'].read_bytes())
        self.assertEqual(value['models_dir'],actual['EnvironmentVariables']['OLLAMA_MODELS'])
        self.assertFalse(selected.exists())
        value['models_dir']=str(self.home/'different models')
        self.p['settings'].write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaises(ValueError):installer.owned(self.p)
    def test_relative_or_checkout_model_root_rejected_before_staging(self):
        for selected in ('relative-models',str(self.source/'weights')):
            with self.subTest(selected=selected),patch.dict(installer.os.environ,{'OLLAMA_MODELS':selected}):
                with self.assertRaises(ValueError):self.prepare()
        self.assertFalse(self.p['settings'].exists());self.assertFalse(self.p['code'].exists())
    def test_disabled_hash_owned_legacy_bundle_upgrades_to_complete_controlled_policy(self):
        self.legacy()
        with patch.object(installer,'worker_locked',return_value=False),patch.object(installer,'launchctl') as command:
            self.assertTrue(self.prepare()['prepared'])
        command.assert_not_called()
        value=installer.settings(self.p)
        self.assertEqual(value['version'],2);self.assertEqual(value['policy'],'controlled-work-v1')
        self.assertIn('journal_collector/work_language.py',value['files'])
        self.assertTrue(plistlib.loads(self.p['plist'].read_bytes())['Disabled'])
        self.assertTrue(self.p['stop'].exists())
    def test_legacy_bundle_cannot_start_or_claim_live_handoff_proof(self):
        value=self.legacy()
        with patch.object(installer,'raw_health') as raw,patch.object(installer,'verify_handoff') as proof,patch.object(installer,'launchctl') as command:
            with self.assertRaises(ValueError):installer.start(self.p,'gui/501')
        raw.assert_not_called();proof.assert_not_called();command.assert_not_called()
        with patch.object(installer.subprocess,'run') as run:
            with self.assertRaises(ValueError):installer.verify_handoff(self.p,value)
        run.assert_not_called()
    def test_legacy_upgrade_refuses_changed_files_enabled_job_and_active_worker(self):
        self.legacy();original=self.p['settings'].read_bytes()
        worker=self.p['code']/'work_narrative_service.py';body=worker.read_bytes();worker.write_bytes(b'tampered')
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(self.p['settings'].read_bytes(),original);worker.write_bytes(body)
        value=installer.settings(self.p,allow_legacy=True)
        self.p['plist'].write_bytes(plistlib.dumps(installer.plist_value(self.p,value,enabled=True)))
        with self.assertRaises(ValueError):self.prepare()
        self.p['plist'].write_bytes(plistlib.dumps(installer.plist_value(self.p,value)))
        with patch.object(installer,'worker_locked',return_value=True):
            with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(self.p['settings'].read_bytes(),original)
    def test_current_policy_never_uses_legacy_completeness_exemption(self):
        self.prepare();value=json.loads(self.p['settings'].read_text(encoding='utf-8'))
        value['files'].pop('journal_collector/work_language.py')
        self.p['settings'].write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaises(ValueError):installer.settings(self.p,allow_legacy=True)
