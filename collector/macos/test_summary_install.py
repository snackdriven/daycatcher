"""Portable summary launchd checks; never operate real launchd or GitHub."""
import importlib.util
import json
from pathlib import Path
import plistlib
from subprocess import CompletedProcess
import sys
import tempfile
import unittest
from unittest.mock import call, patch

spec = importlib.util.spec_from_file_location('summary_install', Path(__file__).with_name('summary_install.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class SummaryInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.p = installer.paths(self.home)
        self.python = sys.executable
        self.gh = self.home / 'gh'
        self.gh.write_bytes(b'fake gh: existence check only')
        self.source = self.home / 'bundle'
        (self.source / 'macos').mkdir(parents=True)
        for name in installer.MODULES:
            (self.source / name).parent.mkdir(parents=True, exist_ok=True)
            (self.source / name).write_text('# fixture\n')
        (self.source / 'macos' / 'summary_install.py').write_text('# fixture installer\n')
        (self.p['code'] / 'macos').mkdir(parents=True)
        (self.p['code'] / 'journal_collector').mkdir()
        (self.p['code'] / 'macos' / 'install.py').write_text('# existing collector installer\n')
        self.p['data'].mkdir(parents=True)
        self.collector_files = [self.p['data'] / 'collector.json', self.p['data'] / 'collector.sqlite3',
                                self.p['code'] / 'unchanged.py', self.p['data'] / 'STOP']
        for path in self.collector_files:
            path.write_bytes(b'preserve exactly')

    def prepare(self):
        return installer.prepare(self.p, self.python, str(self.gh), self.source)

    def test_stage_is_disabled_hourly_and_preserves_collector(self):
        result = self.prepare()
        self.assertFalse(result['enabled'])
        value = plistlib.loads(self.p['plist'].read_bytes())
        self.assertEqual(value['Label'], installer.LABEL)
        self.assertEqual(value['StartInterval'], 3600)
        self.assertTrue(value['RunAtLoad'])
        self.assertTrue(value['Disabled'])
        self.assertFalse(value['KeepAlive'])
        self.assertEqual(value['ProgramArguments'], [self.python, str(self.p['code'] / 'summary_service.py'),
            '--config', str(self.p['config'])])
        config = installer.validate_config(self.p)
        self.assertEqual(config['repository'], 'snackdriven/day-crumbs')
        self.assertFalse(config['enabled'])
        self.assertTrue(self.p['stop'].exists())
        for path in self.collector_files:
            self.assertEqual(path.read_bytes(), b'preserve exactly')

    def test_actual_bundle_imports_after_addon_install(self):
        import subprocess
        source = Path(__file__).resolve().parents[1]
        (self.p['data'] / 'collector.json').unlink()  # Replace the sentinel with a real fixture config.
        installer._base.prepare(installer._base.paths(self.home), self.python, source)
        installer.prepare(self.p, self.python, str(self.gh), source)
        for name in installer.MODULES:
            self.assertTrue((self.p['code'] / name).is_file())
        self.assertFalse((self.p['code'] / 'work_summary.py').exists())
        self.assertFalse((self.p['code'] / 'summary_publish.py').exists())
        result = subprocess.run([self.python, '-c',
            'import summary_service; from journal_collector.work_summary import SummaryQueue; '
            'from journal_collector.summary_publish import Publisher; print("imports-ok")'],
            cwd=self.p['code'], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'imports-ok')
        self.assertFalse((self.p['data'] / 'work-summary.sqlite3').exists())

    def test_require_existing_collector_and_complete_bundle(self):
        (self.p['code'] / 'macos' / 'install.py').unlink()
        with self.assertRaisesRegex(ValueError, 'collector first'):
            self.prepare()
        self.assertFalse(self.p['config'].exists())

    def test_gh_discovery_does_no_network_probe(self):
        with patch.object(installer.subprocess, 'run') as run:
            self.assertEqual(installer.find_gh(str(self.gh)), str(self.gh))
        run.assert_not_called()
        with self.assertRaises(ValueError):
            installer.find_gh('relative-gh')

    def test_reinstall_retains_receipts_and_gh_path(self):
        self.prepare()
        queue = self.p['data'] / 'work-summary.sqlite3'
        queue.write_bytes(b'private receipts')
        self.prepare()
        self.assertEqual(queue.read_bytes(), b'private receipts')
        self.assertEqual(installer.validate_config(self.p)['gh_path'], str(self.gh))

    def test_start_clears_only_summary_stop_and_loads_exact_label(self):
        self.prepare()
        with patch.object(installer, 'loaded', side_effect=[False, True]), \
                patch.object(installer, 'worker_locked', return_value=False), \
                patch.object(installer, 'launchctl') as command:
            result = installer.start(self.p, self.python, 'gui/501')
        self.assertTrue(result['enabled'])
        self.assertFalse(self.p['stop'].exists())
        self.assertEqual((self.p['data'] / 'STOP').read_bytes(), b'preserve exactly')
        self.assertEqual(command.call_args_list, [call('enable', 'gui/501/' + installer.LABEL),
                         call('bootstrap', 'gui/501', str(self.p['plist']))])
        self.assertTrue(installer.validate_config(self.p)['enabled'])

    def test_pending_stop_lock_race_completes_before_start(self):
        self.prepare()
        with patch.object(installer, 'loaded', side_effect=[False, True]), \
                patch.object(installer, 'worker_locked', return_value=True), \
                patch.object(installer, 'stop') as stop, \
                patch.object(installer, 'launchctl'):
            installer.start(self.p, self.python, 'gui/501')
        stop.assert_called_once_with(self.p, 'gui/501')

    def test_stop_waits_for_both_job_and_worker_and_disables_signin(self):
        self.prepare()
        with patch.object(installer, 'loaded', side_effect=[True, True, False, False]), \
                patch.object(installer, 'worker_locked', side_effect=[True, True, False]), \
                patch.object(installer, 'launchctl') as command, \
                patch.object(installer.time, 'sleep') as sleep:
            result = installer.stop(self.p, 'gui/501')
        self.assertTrue(result['stopped'])
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(command.call_args_list, [call('disable', 'gui/501/' + installer.LABEL),
                         call('bootout', 'gui/501/' + installer.LABEL)])
        self.assertTrue(plistlib.loads(self.p['plist'].read_bytes())['Disabled'])
        self.assertFalse(installer.validate_config(self.p)['enabled'])
        for path in self.collector_files:
            self.assertEqual(path.read_bytes(), b'preserve exactly')

    def test_stop_and_status_remain_available_after_gh_removed(self):
        self.prepare()
        self.gh.unlink()
        with patch.object(installer, 'loaded', return_value=False), \
                patch.object(installer, 'worker_locked', return_value=False), \
                patch.object(installer, 'launchctl'):
            self.assertTrue(installer.stop(self.p, 'gui/501')['stopped'])
            self.assertFalse(installer.status(self.p, 'gui/501')['enabled'])

    def test_corrupt_config_cannot_prevent_stop(self):
        self.prepare()
        self.p['config'].write_text('{broken')
        with patch.object(installer, 'loaded', side_effect=[True, False]), \
                patch.object(installer, 'worker_locked', return_value=False), \
                patch.object(installer, 'launchctl') as command:
            result = installer.stop(self.p, 'gui/501')
        self.assertTrue(result['stopped'])
        self.assertTrue(result['config_repair_required'])
        self.assertTrue(self.p['stop'].exists())
        self.assertEqual(self.p['config'].read_text(), '{broken')
        self.assertEqual(command.call_args_list, [call('disable', 'gui/501/' + installer.LABEL),
                         call('bootout', 'gui/501/' + installer.LABEL)])
        for path in self.collector_files:
            self.assertEqual(path.read_bytes(), b'preserve exactly')

    def test_stop_timeout_is_not_reported_as_success(self):
        self.prepare()
        with patch.object(installer, 'loaded', return_value=False), \
                patch.object(installer, 'worker_locked', return_value=True), \
                patch.object(installer, 'launchctl'), \
                patch.object(installer.time, 'monotonic', side_effect=[0, 36]):
            with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
                installer.stop(self.p, 'gui/501')

    def test_status_never_returns_body_strings_or_unknown_fields(self):
        self.prepare()
        (self.p['data'] / 'summary-status.json').write_text(json.dumps({
            'published': 2, 'prepared': 'secret body', 'error': 'private transcript',
            'facts': ['source identifiers'], 'nested': {'body': 'secret'}}))
        with patch.object(installer, 'loaded', return_value=False), \
                patch.object(installer, 'worker_locked', return_value=False):
            result = installer.status(self.p, 'gui/501')
        self.assertEqual(result['last_run_counts'], {'published': 2})
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('transcript', json.dumps(result))

    def test_status_reports_fixed_failure_codes_only(self):
        self.prepare()
        status_path = self.p['data'] / 'summary-status.json'
        for state, error, expected in [
            ('error', 'auth', {'state': 'error', 'error_code': 'auth'}),
            ('review_required', 'manual_edit', {'state': 'review_required', 'error_code': 'manual_edit'}),
            ('private body', 'private token', {}),
            ({'body': 'secret'}, ['secret'], {}),
        ]:
            with self.subTest(state=state, error=error):
                status_path.write_text(json.dumps({'state': state, 'error_code': error}))
                with patch.object(installer, 'loaded', return_value=False), \
                        patch.object(installer, 'worker_locked', return_value=False):
                    result = installer.status(self.p, 'gui/501')
                self.assertEqual({key: result[key] for key in ('state', 'error_code') if key in result}, expected)
                self.assertNotIn('secret', json.dumps(result))
                self.assertNotIn('private', json.dumps(result))

    def test_live_platform_and_prepare_enable_guard(self):
        with patch.object(installer.sys, 'platform', 'win32'), \
                patch.object(installer, 'launchctl') as command:
            self.assertEqual(installer.main(['install']), 1)
            self.assertEqual(installer.main(['install', '--prepare-only', '--enable']), 1)
        command.assert_not_called()

    def test_loaded_uses_summary_label(self):
        with patch.object(installer, 'launchctl', return_value=CompletedProcess([], 0)) as command:
            self.assertTrue(installer.loaded('gui/501'))
        command.assert_called_once_with('print', 'gui/501/' + installer.LABEL, check=False)

    def test_external_destination_rejected_without_overwrite(self):
        self.prepare()
        value = json.loads(self.p['config'].read_text())
        value['repository'] = 'someone/else'
        self.p['config'].write_text(json.dumps(value))
        before = self.p['config'].read_bytes()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.p['config'].read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
