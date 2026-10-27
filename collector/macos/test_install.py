"""Portable installer checks; no launchd or real macOS capture acceptance."""
import importlib.util
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('mac_install', Path(__file__).with_name('install.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # macOS TMPDIR commonly starts with /var, a symlink to /private/var.
        # Test against the real temporary home without weakening installer guards.
        self.home = Path(self.temp.name).resolve()
        self.p = installer.paths(self.home)
        self.python = installer.find_python(sys.executable)

    def prepare(self):
        return installer.prepare(self.p, self.python)

    def test_prepare_private_local_only_config_and_portable_plist(self):
        result = self.prepare()
        config = json.loads(self.p['config'].read_text())
        self.assertFalse(result['live_verified'])
        self.assertFalse(config['claude']['enabled'])
        self.assertEqual(config['data_dir'], str(self.p['data']))
        from datetime import datetime, timezone
        age = datetime.now(timezone.utc) - datetime.fromisoformat(config['local']['since'])
        self.assertAlmostEqual(age.total_seconds(), 86400, delta=5)
        value = plistlib.loads(self.p['plist'].read_bytes())
        self.assertEqual(value['ProgramArguments'], [self.python, '-m', 'journal_collector', 'watch',
            '--config', str(self.p['config'])])
        self.assertTrue(value['RunAtLoad'])
        self.assertEqual(value['KeepAlive'], {'SuccessfulExit': False})
        self.assertEqual(value['Umask'], 0o077)
        self.assertFalse((self.p['code'] / 'claude_bridge').exists())
        self.assertTrue((self.p['code'] / 'macos' / 'install.py').exists())

    def test_reinstall_preserves_config_data_and_initial_horizon(self):
        self.prepare()
        original = self.p['config'].read_bytes()
        database = self.p['data'] / 'collector.sqlite3'
        database.write_bytes(b'private existing data')
        installer.prepare(self.p, self.python)
        self.assertEqual(self.p['config'].read_bytes(), original)
        self.assertEqual(database.read_bytes(), b'private existing data')

    def test_existing_external_runtime_rejected_without_edit(self):
        self.prepare()
        config = json.loads(self.p['config'].read_text())
        config['db'] = '/somewhere/else/private.sqlite3'
        self.p['config'].write_text(json.dumps(config))
        original = self.p['config'].read_bytes()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.p['config'].read_bytes(), original)

    def test_old_python_rejected(self):
        from subprocess import CompletedProcess
        with patch.object(installer.subprocess, 'run', return_value=CompletedProcess([], 0, '0\n', '')):
            with self.assertRaises(ValueError):
                installer.find_python(sys.executable)

    def test_python_stable_symlink_is_preserved_after_probe(self):
        from subprocess import CompletedProcess
        link = self.home / 'stable-python3'
        try:
            link.symlink_to(sys.executable)
        except OSError:
            self.skipTest('Symlink privilege unavailable')
        with patch.object(installer.subprocess, 'run', return_value=CompletedProcess([], 0, '1\n', '')) as probe:
            self.assertEqual(installer.find_python(str(link)), str(link))
        self.assertEqual(probe.call_args.args[0][0], str(link))

    def test_package_directory_symlink_rejected(self):
        target = self.home / 'outside'
        target.mkdir()
        code = self.p['code']
        code.mkdir(parents=True)
        try:
            (code / 'journal_collector').symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink privilege unavailable')
        with self.assertRaises(ValueError):
            installer.paths(self.home)
        self.assertEqual(list(target.iterdir()), [])

    def test_stop_disables_keepalive_and_boots_out_before_success(self):
        self.prepare()
        with patch.object(installer, 'loaded', side_effect=[True, False]), \
                patch.object(installer, 'launchctl') as command:
            result = installer.stop(self.p, 'gui/501')
        self.assertTrue(result['stopped'])
        self.assertTrue((self.p['data'] / 'STOP').exists())
        self.assertIs(plistlib.loads(self.p['plist'].read_bytes())['KeepAlive'], False)
        command.assert_called_once_with('bootout', 'gui/501/' + installer.LABEL)

    def test_start_clears_stop_and_bootstraps_exact_job(self):
        self.prepare()
        (self.p['data'] / 'STOP').touch()
        with patch.object(installer, 'loaded', side_effect=[False, True]), \
                patch.object(installer, 'launchctl') as command:
            result = installer.start(self.p, self.python, 'gui/501')
        self.assertFalse(result['capture_verified'])
        self.assertFalse((self.p['data'] / 'STOP').exists())
        self.assertEqual(command.call_args_list[-1].args, ('bootstrap', 'gui/501', str(self.p['plist'])))

    def test_stop_waits_for_launchd_unload_and_worker_release(self):
        self.prepare()
        with patch.object(installer, 'loaded', side_effect=[True, True, False, False]) as loaded, \
                patch.object(installer, 'worker_locked', side_effect=[True, True, False]) as locked, \
                patch.object(installer, 'launchctl') as command, \
                patch.object(installer.time, 'sleep') as sleep:
            result = installer.stop(self.p, 'gui/501')
        self.assertTrue(result['stopped'])
        self.assertEqual(loaded.call_count, 4)
        self.assertEqual(locked.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        command.assert_called_once_with('bootout', 'gui/501/' + installer.LABEL)

    def test_start_finishes_pending_stop_before_reloading(self):
        self.prepare()
        (self.p['data'] / 'STOP').touch()
        with patch.object(installer, 'loaded', side_effect=[True, True]), \
                patch.object(installer, 'stop') as stop, \
                patch.object(installer, 'launchctl') as command:
            result = installer.start(self.p, self.python, 'gui/501')
        stop.assert_called_once_with(self.p, 'gui/501')
        self.assertNotIn('already_loaded', result)
        self.assertFalse((self.p['data'] / 'STOP').exists())
        self.assertEqual(command.call_args_list[-1].args, ('bootstrap', 'gui/501', str(self.p['plist'])))

    def test_start_finishes_unloaded_but_lock_held_pending_stop(self):
        self.prepare()
        (self.p['data'] / 'STOP').touch()
        with patch.object(installer, 'loaded', side_effect=[False, True]), \
                patch.object(installer, 'worker_locked', return_value=True), \
                patch.object(installer, 'stop') as stop, \
                patch.object(installer, 'launchctl') as command:
            result = installer.start(self.p, self.python, 'gui/501')
        stop.assert_called_once_with(self.p, 'gui/501')
        self.assertNotIn('already_loaded', result)
        self.assertFalse((self.p['data'] / 'STOP').exists())
        self.assertEqual(command.call_args_list[-1].args, ('bootstrap', 'gui/501', str(self.p['plist'])))

    def test_stop_times_out_honestly_if_either_state_remains_active(self):
        self.prepare()
        for still_loaded, still_locked in [(True, False), (False, True)]:
            with self.subTest(launchd_loaded=still_loaded, worker_locked=still_locked), \
                    patch.object(installer, 'loaded', side_effect=[True, still_loaded]), \
                    patch.object(installer, 'worker_locked', return_value=still_locked), \
                    patch.object(installer, 'launchctl'), \
                    patch.object(installer.time, 'monotonic', side_effect=[0, 36]), \
                    patch.object(installer.time, 'sleep') as sleep:
                with self.assertRaisesRegex(RuntimeError, 'stop not confirmed'):
                    installer.stop(self.p, 'gui/501')
                sleep.assert_not_called()

    def test_live_platform_guard_does_not_create_files(self):
        with patch.object(installer.sys, 'platform', 'win32'):
            self.assertEqual(installer.main(['install', '--home', self.temp.name]), 1)
        self.assertFalse(self.p['base'].exists())

    def test_symlink_install_path_rejected(self):
        target = self.home / 'other'
        target.mkdir()
        library = self.home / 'Library'
        try:
            library.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink privilege unavailable')
        with self.assertRaises(ValueError):
            installer.paths(self.home)

    def test_copied_package_status_executes_without_scan(self):
        self.prepare()
        import subprocess
        result = subprocess.run([self.python, '-m', 'journal_collector', 'status', '--config',
            str(self.p['config'])], cwd=self.p['code'], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)
        self.assertIn('workers', status)
        self.assertTrue((self.p['data'] / 'collector.sqlite3').exists())


if __name__ == '__main__':
    unittest.main()
