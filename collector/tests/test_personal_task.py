"""Exercise actual task XML and controller effects with OS task commands intercepted."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
NS = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}
DESCRIPTION = 'Private personal journal summary sync; managed exclusively by collector-core/install_personal_summary.ps1 v1'


@unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), 'Native Windows task controller tests')
class PersonalTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.package = Path(self.temp.name) / 'package'
        self.package.mkdir()
        shutil.copyfile(ROOT / 'install_personal_summary.ps1', self.package / 'install_personal_summary.ps1')
        (self.package / 'personal_summary_service.py').write_text('# not executed by controller tests')
        self.runtime = Path(self.temp.name) / 'runtime & spaces'
        self.runtime.mkdir()
        self.config = self.runtime / 'personal-summary.json'
        self.settings = dict(data_dir=str(self.runtime), source_db=str(self.runtime / 'collector.sqlite3'),
                             queue_db=str(self.runtime / 'personal-summary.sqlite3'),
                             gh_path=str(self.runtime / 'gh.exe'), tzdata_dir=str(self.runtime / 'tzdata'), enabled=True)
        self.config.write_text(json.dumps(self.settings))
        self.pythonw = Path(sys.executable).with_name('pythonw.exe')
        self.calls = Path(self.temp.name) / 'calls.json'

    def run_controller(self, mode, *, exists=True, unrelated=False, running=False, redirected=None, account_name=False):
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        # Definitions shadow every scheduled-task command; no real task can be touched.
        wrapper = r'''
$ErrorActionPreference = 'Stop'
$global:personalTestCalls = [Collections.Generic.List[object]]::new()
function Get-ScheduledTask { param($TaskName,$TaskPath,$ErrorAction)
    if (EXISTS) { [pscustomobject]@{Description=DESCRIPTION; State=STATE;
        Actions=@([pscustomobject]@{Execute=ACTIONEXEC;Arguments=ACTIONARGS;WorkingDirectory=ACTIONDIRECTORY});
        Principal=[pscustomobject]@{UserId=PRINCIPAL;LogonType=LOGONTYPE;RunLevel=RUNLEVEL}} }
}
function Disable-ScheduledTask {param($TaskName,$TaskPath) $global:personalTestCalls.Add(@{op='disable';name=$TaskName})}
function Stop-ScheduledTask {param($TaskName,$TaskPath) $global:personalTestCalls.Add(@{op='stop';name=$TaskName})}
function Unregister-ScheduledTask {param($TaskName,$TaskPath,$Confirm) $global:personalTestCalls.Add(@{op='remove';name=$TaskName})}
function Enable-ScheduledTask {param($TaskName,$TaskPath) $global:personalTestCalls.Add(@{op='enable';name=$TaskName})}
function Start-ScheduledTask {param($TaskName,$TaskPath) $global:personalTestCalls.Add(@{op='start';name=$TaskName})}
function Register-ScheduledTask {param($TaskName,$TaskPath,$Xml,[switch]$Force) $global:personalTestCalls.Add(@{op='register';name=$TaskName;xml=$Xml})}
function Export-ScheduledTask {param($TaskName,$TaskPath) '<Task />'}
function Get-ScheduledTaskInfo {param($TaskName,$TaskPath) [pscustomobject]@{LastTaskResult=0}}
try { & SCRIPT -Config CONFIG -Mode MODE -PythonwPath PYTHONW }
finally { ConvertTo-Json -InputObject @($global:personalTestCalls.ToArray()) -Depth 10 | Set-Content -LiteralPath CALLS }
'''
        for key, value in {'EXISTS': '$true' if exists else '$false',
                           'DESCRIPTION': quote('unrelated' if unrelated else DESCRIPTION),
                           'STATE': quote('Running' if running else 'Disabled'),
                           'ACTIONEXEC': quote('C:\\redirected\\pythonw.exe' if redirected == 'executable' else self.pythonw),
                           'ACTIONARGS': quote(f'"{self.package / "personal_summary_service.py"}" --config "{self.runtime / ("other.json" if redirected == "config" else "personal-summary.json")}"'),
                           'ACTIONDIRECTORY': quote(self.runtime if redirected == 'directory' else self.package),
                           'PRINCIPAL': quote('S-1-5-18') if redirected == 'principal' else ('[Security.Principal.WindowsIdentity]::GetCurrent().Name' if account_name else '[Security.Principal.WindowsIdentity]::GetCurrent().User.Value'),
                           'LOGONTYPE': quote('Password' if redirected == 'logon' else 'Interactive'),
                           'RUNLEVEL': quote('Highest' if redirected == 'runlevel' else 'Limited'),
                           'SCRIPT': quote(self.package / 'install_personal_summary.ps1'), 'CONFIG': quote(self.config),
                           'MODE': quote(mode), 'PYTHONW': quote(self.pythonw), 'CALLS': quote(self.calls)}.items():
            wrapper = wrapper.replace(key, value)
        launcher = Path(self.temp.name) / 'harness.ps1'
        launcher.write_text(wrapper)
        result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File', str(launcher)],
                                capture_output=True, text=True, timeout=25)
        calls = json.loads(self.calls.read_text(encoding='utf-8-sig'))
        return result, calls

    def test_plan_parses_real_xml_and_is_read_only(self):
        original = self.config.read_bytes()
        result, calls = self.run_controller('Plan')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        xml = ET.fromstring(plan['xml'])
        text = lambda path: xml.find(path, NS).text
        self.assertEqual(text('t:Settings/t:Enabled'), 'false')
        self.assertEqual(text('t:Principals/t:Principal/t:LogonType'), 'InteractiveToken')
        self.assertEqual(text('t:Principals/t:Principal/t:RunLevel'), 'LeastPrivilege')
        self.assertTrue(text('t:Principals/t:Principal/t:UserId').startswith('S-1-'))
        self.assertEqual(text('t:Triggers/t:TimeTrigger/t:Repetition/t:Interval'), 'PT1H')
        self.assertIsNone(xml.find('t:Triggers/t:TimeTrigger/t:Repetition/t:Duration', NS))
        self.assertEqual(text('t:Triggers/t:LogonTrigger/t:UserId'), text('t:Principals/t:Principal/t:UserId'))
        self.assertEqual(text('t:Actions/t:Exec/t:Command'), str(self.pythonw))
        self.assertEqual(text('t:Actions/t:Exec/t:Arguments'),
                         f'"{self.package / "personal_summary_service.py"}" --config "{self.config}"')
        self.assertEqual(calls, [])
        self.assertEqual(self.config.read_bytes(), original)
        self.assertFalse((self.runtime / 'personal-summary-STOP').exists())

    def test_install_registers_disabled_xml_and_never_launches(self):
        result, calls = self.run_controller('Install', exists=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c['op'] for c in calls], ['register'])
        xml = ET.fromstring(calls[0]['xml'])
        self.assertEqual(xml.find('t:Settings/t:Enabled', NS).text, 'false')
        self.assertFalse(json.loads(self.config.read_text())['enabled'])

    def test_start_reenables_atomic_config_and_requests_launch(self):
        stop = self.runtime / 'personal-summary-STOP'
        stop.write_text('stop')
        self.settings['enabled'] = False
        self.config.write_text(json.dumps(self.settings))
        result, calls = self.run_controller('Start')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c['op'] for c in calls], ['enable', 'start'])
        self.assertTrue(json.loads(self.config.read_text())['enabled'])
        self.assertFalse(stop.exists())
        self.assertEqual(list(self.runtime.glob('*.tmp')), [])

    def test_stop_ignores_corrupt_json_and_only_stops_own_task(self):
        self.config.write_text('{broken')
        result, calls = self.run_controller('Stop', running=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.runtime / 'personal-summary-STOP').exists())
        self.assertEqual([c['op'] for c in calls], ['disable', 'stop'])
        self.assertTrue(all(c['name'] == 'Personal Journal Sync - ' + os.environ['USERNAME'] for c in calls))
        self.assertTrue(json.loads(result.stdout)['stopped'])

    def test_stop_missing_configuration_still_stops(self):
        self.config.unlink()
        result, calls = self.run_controller('Stop')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.runtime / 'personal-summary-STOP').exists())
        self.assertEqual([c['op'] for c in calls], ['disable'])

    def test_stop_waits_for_real_windows_worker_handle_to_release(self):
        import msvcrt
        lock_dir = self.runtime / '.personal-summary-worker'
        lock_dir.mkdir()
        locked = threading.Event()

        def worker():
            with (lock_dir / 'local-worker.lock').open('a+b') as stream:
                stream.write(b'0')
                stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                locked.set()
                # Model the real worker noticing STOP and finishing its bounded pass.
                deadline = time.monotonic() + 10
                while not (self.runtime / 'personal-summary-STOP').exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                time.sleep(0.3)

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(locked.wait(2))
        try:
            result, calls = self.run_controller('Stop', running=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['stopped'])
            self.assertFalse(thread.is_alive(), 'Controller must poll until real lock handle is closed')
            self.assertEqual([c['op'] for c in calls], ['disable', 'stop'])
        finally:
            thread.join(12)

    def test_status_does_not_need_valid_configuration(self):
        self.config.write_text('{broken')
        result, calls = self.run_controller('Status')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['installed'])
        self.assertEqual(calls, [])

    def test_unrelated_task_is_never_changed(self):
        original = self.config.read_bytes()
        result, calls = self.run_controller('Install', unrelated=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        self.assertEqual(self.config.read_bytes(), original)

    def test_wrong_runtime_binding_cannot_enable_task(self):
        self.settings['queue_db'] = self.settings['source_db']
        self.config.write_text(json.dumps(self.settings))
        result, calls = self.run_controller('Start')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_same_description_redirected_task_cannot_be_mutated(self):
        original = self.config.read_bytes()
        for mode in ('Start', 'Stop', 'Remove', 'Install'):
            for redirected in ('executable', 'config', 'directory', 'principal', 'logon', 'runlevel'):
                with self.subTest(mode=mode, redirected=redirected):
                    stop = self.runtime / 'personal-summary-STOP'
                    stop.unlink(missing_ok=True)
                    result, calls = self.run_controller(mode, running=True, redirected=redirected)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('unrelated or redirected', result.stderr)
                    self.assertEqual(calls, [])
                    self.assertEqual(self.config.read_bytes(), original)
                    self.assertEqual(stop.exists(), mode in ('Stop', 'Remove'))

    def test_current_account_principal_is_normalized_to_current_sid(self):
        result, calls = self.run_controller('Stop', account_name=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c['op'] for c in calls], ['disable'])

    def test_remove_keeps_all_data(self):
        database = self.runtime / 'personal-summary.sqlite3'
        database.write_bytes(b'retained')
        result, calls = self.run_controller('Remove', running=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c['op'] for c in calls], ['disable', 'stop', 'remove'])
        self.assertEqual(database.read_bytes(), b'retained')
        self.assertTrue(self.config.exists())


if __name__ == '__main__':
    unittest.main()
