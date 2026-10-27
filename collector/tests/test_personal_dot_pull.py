import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import personal_summary_service as service
import pull_dot_handoff


class PersonalDotPullTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.path = self.data/'personal-summary.json'
        self.config = dict(profile='personal', enabled=True, data_dir=str(self.data),
            source_db=str(self.data/'collector.sqlite3'), queue_db=str(self.data/'personal-summary.sqlite3'),
            tzdata_dir=str(self.data/'tzdata'), tzdata_sha256='a'*64,
            gh_path=str(self.data/'gh.exe'), repository='snackdriven/day-crumbs', branch='main')

    def load(self, **changes):
        self.path.write_text(json.dumps(dict(self.config, **changes)))
        return service.load_config(self.path)

    def test_loader_defaults_and_optional_bounds(self):
        config = self.load()
        self.assertIs(config['dot_handoff_enabled'], False)
        self.assertEqual(config['dot_handoff_days'], 3)
        for days in [1,14]:
            config = self.load(dot_handoff_enabled=True,dot_handoff_days=days)
            self.assertIs(config['dot_handoff_enabled'], True)
            self.assertEqual(config['dot_handoff_days'], days)

    def test_loader_rejects_invalid_dot_options(self):
        for item in [None,1,0,'true',[],{}]:
            with self.subTest(enabled=item), self.assertRaises(ValueError):
                self.load(dot_handoff_enabled=item)
        for item in [None,0,15,True,False,1.0,'3',[],{}]:
            with self.subTest(days=item), self.assertRaises(ValueError):
                self.load(dot_handoff_days=item)

    def test_pull_runs_after_timezone_before_existing_personal_queue(self):
        events = []
        config = self.load(dot_handoff_enabled=True,dot_handoff_days=2)
        def timezone(value):
            self.assertIs(value, config)
            events.append('timezone')
        def pull(value, days):
            self.assertIs(value, config)
            self.assertEqual(days,2)
            events.append('pull')
            return {'state':'idle','new':2,'files':1}
        def base(value, **kwargs):
            events.append('queue')
            self.assertIs(value,config)
            self.assertFalse(kwargs['dry_run'])
            self.assertEqual(kwargs['runtime']['stop'],'personal-summary-STOP')
            return {'state':'idle','published':2}
        with patch.object(service,'configure_timezone',side_effect=timezone), \
             patch.object(pull_dot_handoff,'pull',side_effect=pull), \
             patch.object(service.base,'execute',side_effect=base):
            result = service.execute(config)
        self.assertEqual(events,['timezone','pull','queue'])
        self.assertEqual(result,{'state':'idle','published':2,
            'dot_handoff':{'state':'idle','new':2,'files':1}})

    def test_dot_failure_does_not_block_other_personal_sources(self):
        config = self.load(dot_handoff_enabled=True)
        for response in [RuntimeError('PRIVATE remote error body'),
                         {'state':'error','error_code':'invalid_handoff'},
                         {'state':'error','error_code':'handoff_conflict'}]:
            with self.subTest(response_type=type(response).__name__), \
                 patch.object(service,'configure_timezone'), \
                 patch.object(pull_dot_handoff,'pull',side_effect=response if isinstance(response,Exception) else None,
                              return_value=response), \
                 patch.object(service.base,'execute',return_value={'state':'idle','published':1}) as base:
                result = service.execute(config)
                base.assert_called_once()
                self.assertEqual(result['published'],1)
                self.assertEqual(result['state'],'idle')
                self.assertEqual(result['dot_handoff']['state'],'error')
                self.assertNotIn('PRIVATE',json.dumps(result))

    def test_default_disabled_dry_run_and_personal_stop_never_pull(self):
        for settings,dry_run,stop in [({},False,False),
            ({'dot_handoff_enabled':True},True,False),
            ({'dot_handoff_enabled':True,'enabled':False},False,False),
            ({'dot_handoff_enabled':True},False,True)]:
            config = self.load(**settings)
            stop_path = self.data/'personal-summary-STOP'
            if stop:
                stop_path.touch()
            with self.subTest(settings=settings,dry_run=dry_run,stop=stop), \
                 patch.object(service,'configure_timezone'), \
                 patch.object(pull_dot_handoff,'pull') as pull, \
                 patch.object(service.base,'execute',return_value={'state':'idle'}) as base:
                self.assertEqual(service.execute(config,dry_run=dry_run),{'state':'idle'})
                pull.assert_not_called()
                base.assert_called_once()
            if stop:
                stop_path.unlink()

    def test_busy_base_health_is_preserved_without_rewriting_status(self):
        config = self.load(dot_handoff_enabled=True)
        health = {'state':'busy','enabled':True}
        status = self.data/'personal-summary-status.json'
        status.write_text('{"state":"running","pid":123}')
        with patch.object(service,'configure_timezone'), \
             patch.object(pull_dot_handoff,'pull',return_value={'state':'idle','new':0}), \
             patch.object(service.base,'execute',return_value=health):
            result = service.execute(config)
        self.assertEqual(result['state'],'busy')
        self.assertEqual(status.read_text(),'{"state":"running","pid":123}')
        self.assertEqual(health,{'state':'busy','enabled':True})

    def test_only_fixed_metadata_from_dot_pull_reaches_worker_output(self):
        config = self.load(dot_handoff_enabled=True)
        with patch.object(service,'configure_timezone'), \
             patch.object(pull_dot_handoff,'pull',return_value={'state':'error',
                 'error_code':'PRIVATE exception body','raw':'PRIVATE transcript','files':1}), \
             patch.object(service.base,'execute',return_value={'state':'idle'}):
            result = service.execute(config)
        self.assertEqual(result['dot_handoff'], {'state':'error','error_code':'dot_pull_failed','files':1})
        self.assertNotIn('PRIVATE',json.dumps(result))


if __name__ == '__main__':
    unittest.main()
