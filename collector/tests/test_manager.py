import json
import io
import os
from contextlib import redirect_stderr
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import manage

ROOT = Path(__file__).resolve().parents[1]


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / 'data'
        subprocess.run([sys.executable, '-m', 'journal_collector', 'init', '--data-dir', str(self.data)], cwd=ROOT, check=True, capture_output=True)
        self.config = self.data / 'collector.json'
        config = json.loads(self.config.read_text()); config['local']['codex_roots'] = []; config['local']['claude_roots'] = []
        config['poll_seconds'] = 0.1
        self.config.write_text(json.dumps(config))

    def call(self, action, ok=True):
        result = subprocess.run([sys.executable, 'manage.py', action, '--config', str(self.config)], cwd=ROOT,
            capture_output=True, text=True, timeout=20)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        return result

    def tearDown(self):
        if (ROOT / 'manage.py').exists():
            self.call('stop')
        time.sleep(0.15)
        self.temp.cleanup()

    def test_start_is_independent_process_and_duplicate_start_is_rejected(self):
        started = self.call('start')
        self.assertTrue(started['local']['running'])
        self.assertNotEqual(started['local']['pid'], started['manager_pid'])
        self.assertNotEqual(self.call('start', ok=False).returncode, 0)
        self.assertTrue(self.call('status')['workers']['local']['state'] == 'running')

    def test_stop_confirms_worker_exit_and_start_can_resume(self):
        self.call('start')
        self.assertTrue(self.call('stop')['stopped'])
        self.assertTrue((self.data / 'STOP').exists())
        self.assertTrue(self.call('start')['local']['running'])
        self.assertFalse((self.data / 'STOP').exists())

    def test_invalid_bridge_launch_leaves_no_local_worker_running(self):
        config = json.loads(self.config.read_text())
        config['claude'] = {'enabled': True, 'electron': str(self.data / 'missing.exe'), 'config': str(self.data / 'bridge.json')}
        self.config.write_text(json.dumps(config))
        self.assertNotEqual(self.call('start', ok=False).returncode, 0)
        self.assertFalse(manage.local_is_locked(self.data))

    def test_health_timeout_cleans_up_the_process_started_by_this_call(self):
        class FakeProcess:
            pid = 77777777
            terminated = False
            def poll(self): return 0 if self.terminated else None
            def terminate(self): self.terminated = True
            def kill(self): self.terminated = True
            def wait(self, timeout=None):
                if not self.terminated:
                    raise subprocess.TimeoutExpired('synthetic', timeout)
                return 0
        process = FakeProcess()
        with mock.patch.object(manage, 'launch', return_value=process), mock.patch.object(manage, 'await_health', side_effect=RuntimeError('synthetic timeout')), redirect_stderr(io.StringIO()):
            self.assertEqual(manage.main(['start', '--config', str(self.config)]), 1)
        self.assertTrue(process.terminated)

    def test_relative_runtime_config_is_rejected_before_launch(self):
        original = self.config.read_text()
        config = json.loads(original); config['data_dir'] = 'relative'; config['db'] = 'relative/db.sqlite3'
        self.config.write_text(json.dumps(config))
        try:
            with mock.patch.object(manage, 'launch') as launch, redirect_stderr(io.StringIO()):
                self.assertEqual(manage.main(['start', '--config', str(self.config)]), 1)
                launch.assert_not_called()
        finally:
            self.config.write_text(original)

    def test_status_does_not_report_stale_health_as_running(self):
        (self.data / 'local-status.json').write_text(json.dumps({'state': 'running', 'pid': 77777777}))
        (self.data / 'claude-status.json').write_text(json.dumps({'active': True, 'pid': 77777777}))
        state = self.call('status')
        self.assertEqual(state['workers']['local']['state'], 'stopped')
        self.assertFalse(state['workers']['claude']['active'])

    def test_dead_bridge_pid_does_not_permanently_block_start_or_stop(self):
        (self.data / 'claude-bridge.lock').write_text('77777777')
        self.assertTrue(self.call('start')['local']['running'])
        self.assertTrue(self.call('stop')['stopped'])

    @unittest.skipUnless(os.name == 'nt', 'Windows virtual environment redirector')
    def test_windows_venv_worker_starts_stops_and_failed_start_releases_lock(self):
        import venv
        environment = Path(self.temp.name) / 'venv'
        venv.create(environment, with_pip=False)
        python = environment / 'Scripts' / 'python.exe'
        started = subprocess.run([str(python), 'manage.py', 'start', '--config', str(self.config)],
                                 cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertTrue(self.call('stop')['stopped'])
        self.assertFalse(manage.local_is_locked(self.data))
        # Start a real redirector child, then model a later bridge startup failure.
        (self.data / 'STOP').unlink()
        process = manage.launch([str(python), '-m', 'journal_collector', 'watch',
                                 '--config', str(self.config)], self.data / 'venv-worker.log')
        try:
            manage.await_health(process, self.data / 'local-status.json')
            manage.cleanup_started([process], self.data)
            self.assertFalse(manage.local_is_locked(self.data))
        finally:
            self.call('stop')
            if process.poll() is None:
                process.terminate(); process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
