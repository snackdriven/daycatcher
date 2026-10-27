import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

import startup


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Match startup's canonical config binding on macOS /var temp aliases.
        root = Path(self.temp.name).resolve()
        self.data = root / 'runtime'
        self.config = root / 'collector.json'
        self.config.write_text(json.dumps({'data_dir': str(self.data), 'db': str(self.data / 'db.sqlite3'), 'claude': {'enabled': True}}))
        self.running = {'local': {'state': 'running'}, 'claude': {'active': True}}

    def call(self):
        return startup.main(['--config', str(self.config)])

    def status(self):
        return json.loads((self.data / 'startup-status.json').read_text())

    def test_existing_workers_are_idempotent_with_absent_pythonw_streams(self):
        with mock.patch.object(startup, 'worker_health', return_value=self.running), mock.patch.object(startup.manage, 'main') as manager, mock.patch.object(sys, 'stdout', None), mock.patch.object(sys, 'stderr', None):
            self.assertEqual(self.call(), 0)
            self.assertEqual(self.call(), 0)
        manager.assert_not_called()
        self.assertEqual(self.status()['event'], 'already_running')

    def test_cold_start_resumes_after_manual_stop(self):
        self.data.mkdir()
        (self.data / 'STOP').write_text('manual stop')
        with mock.patch.object(startup, 'worker_health', side_effect=[{}, self.running]), mock.patch.object(startup.manage, 'local_is_locked', return_value=False), mock.patch.object(startup.manage, 'bridge_is_locked', return_value=False), mock.patch.object(startup.manage, 'main', return_value=0) as manager:
            self.assertEqual(self.call(), 0)
        manager.assert_called_once_with(['start', '--config', str(self.config)])
        self.assertEqual(self.status()['event'], 'started')

    def test_partial_workers_recover_via_graceful_stop_then_start(self):
        with mock.patch.object(startup, 'worker_health', side_effect=[{'local': {'state': 'running'}}, self.running]), mock.patch.object(startup.manage, 'main', return_value=0) as manager:
            self.assertEqual(self.call(), 0)
        self.assertEqual([call.args[0][0] for call in manager.call_args_list], ['stop', 'start'])
        self.assertEqual(self.status()['event'], 'recovered')

    def test_failed_partial_stop_never_starts_another_worker(self):
        with mock.patch.object(startup, 'worker_health', return_value={'local': {'state': 'running'}}), mock.patch.object(startup.manage, 'main', return_value=1) as manager:
            self.assertEqual(self.call(), 1)
        manager.assert_called_once_with(['stop', '--config', str(self.config)])
        self.assertEqual(self.status()['event'], 'recovery_failed')

    def test_unhealthy_start_does_not_claim_success(self):
        with mock.patch.object(startup, 'worker_health', return_value={}), mock.patch.object(startup.manage, 'local_is_locked', return_value=False), mock.patch.object(startup.manage, 'bridge_is_locked', return_value=False), mock.patch.object(startup.manage, 'main', return_value=0):
            self.assertEqual(self.call(), 1)
        self.assertEqual(self.status()['event'], 'start_failed')

    def test_manager_failure_race_is_success_if_workers_now_healthy(self):
        with mock.patch.object(startup, 'worker_health', side_effect=[{}, self.running]), mock.patch.object(startup.manage, 'local_is_locked', return_value=False), mock.patch.object(startup.manage, 'bridge_is_locked', return_value=False), mock.patch.object(startup.manage, 'main', return_value=1):
            self.assertEqual(self.call(), 0)

    def test_errors_and_manager_output_never_leak_bodies_or_need_stdout(self):
        secret = 'PRIVATE MESSAGE BODY'
        def fail(_):
            print(secret)
            print(secret, file=sys.stderr)
            raise RuntimeError(secret)
        with mock.patch.object(startup, 'worker_health', return_value={}), mock.patch.object(startup.manage, 'local_is_locked', return_value=False), mock.patch.object(startup.manage, 'bridge_is_locked', return_value=False), mock.patch.object(startup.manage, 'main', side_effect=fail), mock.patch.object(sys, 'stdout', None), mock.patch.object(sys, 'stderr', None):
            self.assertEqual(self.call(), 1)
        for name in ('startup-status.json', 'startup.log'):
            self.assertNotIn(secret, (self.data / name).read_text())
        self.assertEqual(self.status()['event'], 'startup_error')

    def test_invalid_config_records_fixed_error_beside_config(self):
        self.config.write_text('{private broken config')
        with mock.patch.object(sys, 'stderr', None):
            self.assertEqual(self.call(), 1)
        value = json.loads((self.config.parent / 'startup-status.json').read_text())
        self.assertEqual(value['event'], 'startup_error')
        self.assertNotIn('private', (self.config.parent / 'startup.log').read_text())

    def test_disabled_bridge_only_requires_local_worker(self):
        value = json.loads(self.config.read_text()); value['claude']['enabled'] = False
        self.config.write_text(json.dumps(value))
        with mock.patch.object(startup, 'worker_health', return_value={'local': {'state': 'running'}}), mock.patch.object(startup.manage, 'main') as manager:
            self.assertEqual(self.call(), 0)
        manager.assert_not_called()

    def test_startup_lock_serializes_invocations(self):
        acquired = threading.Event()
        trying = threading.Event()
        def contender():
            trying.set()
            with startup.startup_lock(self.data):
                acquired.set()
        with startup.startup_lock(self.data):
            thread = threading.Thread(target=contender)
            thread.start()
            self.assertTrue(trying.wait(1))
            self.assertFalse(acquired.wait(0.15))
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(acquired.is_set())

    def test_unknown_bridge_lock_fails_without_starting_or_stopping(self):
        with mock.patch.object(startup, 'worker_health', return_value={}), mock.patch.object(startup.manage, 'local_is_locked', return_value=False), mock.patch.object(startup.manage, 'bridge_is_locked', side_effect=RuntimeError('private detail')), mock.patch.object(startup.manage, 'main') as manager:
            self.assertEqual(self.call(), 1)
        manager.assert_not_called()
        self.assertEqual(self.status()['event'], 'startup_error')
    def test_alias_config_is_resolved_before_manager_invocation(self):
        alias=self.config.with_name('pretend-var-config.json')
        original=Path.resolve
        # Safely reproduce two spellings of one config on every platform.
        def resolved(path,*args,**kwargs):
            return self.config if path==alias else original(path,*args,**kwargs)
        with mock.patch.object(Path,'resolve',autospec=True,side_effect=resolved),mock.patch.object(startup,'worker_health',side_effect=[{},self.running]),mock.patch.object(startup.manage,'local_is_locked',return_value=False),mock.patch.object(startup.manage,'bridge_is_locked',return_value=False),mock.patch.object(startup.manage,'main',return_value=0) as manager:
            self.assertEqual(0,startup.main(['--config',str(alias)]))
        manager.assert_called_once_with(['start','--config',str(self.config)])
