"""Portable fake-home tests; no launchctl or live source mutation."""
import importlib.util
import json
from pathlib import Path
import plistlib
import unittest
from unittest.mock import patch, call, Mock

import test_narrative_install as fixture_module

spec=importlib.util.spec_from_file_location('history_install',Path(__file__).with_name('history_install.py'))
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class HistoryInstallerTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture_module.InstallerTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare()
        self.home=self.fixture.home;self.p=installer.paths(self.home);self.source=self.fixture.source
        for name in ('history_collect.py','work_history.py','store.py','local.py'):
            (self.source/'journal_collector'/name).write_text('# fixture\n')
        (self.source/'work_history_service.py').write_text('# fixture\n')
        (self.source/'macos'/'history_install.py').write_text('# fixture\n')
        original=self.fixture.p
        self.originals={path:path.read_bytes() for path in (
            original['settings'],original['plist'],original['config'],original['raw_plist'])}

    def test_stage_disabled_preserves_all_existing_controls_and_hashes(self):
        with patch.object(installer,'launchctl') as launchctl:
            result=installer.prepare(self.p,self.source)
        launchctl.assert_not_called();self.assertFalse(result['enabled'])
        _,plist=installer.owned(self.p)
        self.assertTrue(plist['Disabled']);self.assertTrue(self.p['stop'].exists())
        self.assertIn('--once',plist['ProgramArguments'])
        for path,body in self.originals.items():self.assertEqual(path.read_bytes(),body)

    def test_modified_privacy_implementation_refuses_stage(self):
        (self.source/'journal_collector'/'local_work_model.py').write_text('# changed\n')
        with self.assertRaisesRegex(ValueError,'controlled_implementation_changed'):
            installer.prepare(self.p,self.source)
        self.assertFalse(self.p['code'].exists())

    def test_inventory_executes_exact_pinned_readonly_mode_and_filters_output(self):
        installer.prepare(self.p,self.source)
        result=Mock(returncode=0,stdout=json.dumps({'state':'inventory','eligible_days':4,
            'arbitrary_source':'PRIVATE TITLE'}))
        with patch.object(installer.subprocess,'run',return_value=result) as run:
            output=installer.inspect(self.p,'inventory')
        command=run.call_args.args[0]
        self.assertIn('--inventory-only',command);self.assertNotIn('--publish',command)
        self.assertNotIn('PRIVATE',json.dumps(output));self.assertEqual(output['eligible_days'],4)

    def test_active_reinstall_refused_and_tampered_code_detected(self):
        installer.prepare(self.p,self.source)
        value,_=installer.owned(self.p)
        self.p['plist'].write_bytes(plistlib.dumps(installer.plist_value(self.p,value,True)))
        with self.assertRaisesRegex(ValueError,'stop_before_reinstall'):installer.prepare(self.p,self.source)
        self.p['plist'].write_bytes(plistlib.dumps(installer.plist_value(self.p,value)))
        (self.p['code']/'work_history_service.py').write_text('# changed')
        with self.assertRaises(ValueError):installer.owned(self.p)

    def test_start_requires_real_handoff_before_clearing_stop_or_loading(self):
        installer.prepare(self.p,self.source)
        with patch.object(installer.base,'raw_health'),patch.object(installer.base,'verify_handoff',side_effect=ValueError('no proof')),patch.object(installer,'launchctl') as launchctl:
            with self.assertRaises(ValueError):installer.start(self.p,'gui/501')
        launchctl.assert_not_called();self.assertTrue(self.p['stop'].exists())

    def test_start_and_stop_only_own_current_user_job(self):
        installer.prepare(self.p,self.source)
        with patch.object(installer.base,'raw_health'),patch.object(installer.base,'verify_handoff',return_value={'moments':1}),patch.object(installer,'worker_locked',return_value=False),patch.object(installer,'loaded',side_effect=[False,True]),patch.object(installer,'launchctl') as launchctl:
            self.assertTrue(installer.start(self.p,'gui/501')['enabled'])
        self.assertEqual(launchctl.call_args_list,[call('enable','gui/501/'+installer.LABEL),call('bootstrap','gui/501',str(self.p['plist']))])
        with patch.object(installer,'loaded',return_value=False),patch.object(installer,'worker_locked',return_value=False),patch.object(installer,'launchctl') as launchctl:
            self.assertTrue(installer.stop(self.p,'gui/501')['stopped'])
        self.assertEqual(launchctl.call_args_list,[call('disable','gui/501/'+installer.LABEL)])
        for path,body in self.originals.items():self.assertEqual(path.read_bytes(),body)


if __name__=='__main__':unittest.main()
