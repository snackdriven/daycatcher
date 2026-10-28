import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from discover_work_history import fresh_state, discover, valid_days, PREFIX


def body(day):
    return json.dumps({'version': 1, 'day': day, 'timezone': 'America/Chicago', 'cutoff': '03:00',
        'source': 'work', 'processing': 'on-device', 'coverage': 'partial', 'moments': []}).encode()


class Remote:
    def __init__(self): self.files = {}; self.calls = []; self.private = True; self.unavailable = set()
    def repo(self):
        self.calls.append('verify')
        return {'full_name': 'snackdriven/day-crumbs', 'private': self.private, 'default_branch': 'main', 'permissions': {'push': True}}
    def add(self, day, raw=None):
        raw = body(day) if raw is None else raw
        sha = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        self.files[day] = {'name': day+'.json', 'path': PREFIX+'/'+day+'.json', 'type': 'file', 'sha': sha, 'size': len(raw), 'content': base64.b64encode(raw).decode(), 'encoding': 'base64'}
    def _api(self, endpoint, **kwargs):
        self.calls.append(endpoint)
        if '/contents/'+PREFIX+'?' in endpoint:
            return [{k: v for k, v in value.items() if k not in {'content', 'encoding'}} for value in self.files.values()]
        day = endpoint.split('/')[-1].split('.')[0]
        if day in self.unavailable: raise RuntimeError('unavailable')
        return dict(self.files[day])


class DiscoverWorkHistoryTests(unittest.TestCase):
    def setUp(self):
        try: ZoneInfo('America/Chicago')
        except Exception: self.skipTest('Supply pinned IANA data')
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.config = {'data_dir': self.temp.name, 'gh_path': 'unused'}
        self.remote = Remote(); self.state = fresh_state()

    def test_bounded_changed_handoffs_resume_and_metadata_only_output(self):
        for n in range(1, 25): self.remote.add(f'2020-01-{n:02}')
        first = discover(self.config, self.state, self.remote)
        self.assertEqual(first['body_reads'], 20); self.assertEqual(len(valid_days(self.state)['days']), 20)
        second = discover(self.config, self.state, self.remote)
        self.assertEqual(second['body_reads'], 4); self.assertEqual(len(valid_days(self.state)['days']), 24)
        self.assertEqual(discover(self.config, self.state, self.remote)['body_reads'], 0)
        self.assertNotIn('moments', json.dumps(self.state)); self.assertNotIn('content', json.dumps(self.state))

    def test_verifies_private_repo_before_directory_read_and_honors_stop(self):
        self.remote.private = False
        with self.assertRaises(Exception): discover(self.config, self.state, self.remote)
        self.assertEqual(self.remote.calls, ['verify'])
        self.remote.calls = []
        data = Path(self.temp.name)
        for stop in (data/'history'/'STOP', data/'narrative-STOP', data/'work-handoff-STOP', data/'narrative-history'/'STOP'):
            stop.parent.mkdir(exist_ok=True); stop.touch()
            with self.assertRaises(ValueError): discover(self.config, self.state, self.remote)
            self.assertEqual(self.remote.calls, []); stop.unlink()

    def test_sha_binding_invalid_freeform_and_unavailable_preservation(self):
        day = '2020-01-01'; self.remote.add(day); discover(self.config, self.state, self.remote)
        original = valid_days(self.state)
        self.remote.add(day, body(day)+b' '); self.remote.unavailable.add(day)
        result = discover(self.config, self.state, self.remote)
        self.assertEqual(result['unavailable'], 1); self.assertEqual(valid_days(self.state), original)
        self.remote.unavailable.clear()
        value = json.loads(body(day)); value['moments'] = [{'id': 'a'*64, 'time': day+'T15:00:00Z', 'kind': 'intent', 'text': 'PRIVATE_UNSAFE_SENTINEL', 'sanitized': True}]
        self.remote.add(day, json.dumps(value).encode())
        result = discover(self.config, self.state, self.remote)
        self.assertEqual(result['invalid'], 1); self.assertEqual(valid_days(self.state)['days'], [])
        self.assertNotIn('PRIVATE_UNSAFE_SENTINEL', json.dumps(self.state))

    def test_response_sha_and_path_mismatch_never_validates(self):
        day = '2020-01-01'; self.remote.add(day)
        original = self.remote._api
        def altered(endpoint, **kwargs):
            result = original(endpoint, **kwargs)
            if isinstance(result, dict): result['path'] = 'other/path.json'
            return result
        self.remote._api = altered
        result = discover(self.config, self.state, self.remote)
        self.assertEqual(result['invalid'], 1); self.assertEqual(valid_days(self.state)['days'], [])

    def test_directory_limit_is_explicit_gap_and_missing_directory_retains_valid(self):
        day = '2020-01-01'; self.remote.add(day); discover(self.config, self.state, self.remote)
        original = valid_days(self.state)
        self.remote._api = lambda *a, **k: None
        result = discover(self.config, self.state, self.remote)
        self.assertFalse(result['directory_complete']); self.assertEqual(valid_days(self.state), original)
        # A full directory result has no pagination/end proof. Do not erase
        # previously validated dates merely absent from this truncated list.
        metadata = {'type': 'file', 'name': 'ignore.txt', 'path': PREFIX+'/ignore.txt', 'sha': 'a'*40, 'size': 0}
        self.remote._api = lambda *a, **k: [metadata]*1000
        result = discover(self.config, self.state, self.remote)
        self.assertTrue(result['directory_limit_gap']); self.assertFalse(result['directory_complete'])
        self.assertEqual(valid_days(self.state), original)

    def test_declared_sha_does_not_match_decoded_git_blob(self):
        day = '2020-01-01'; self.remote.add(day)
        self.remote.files[day]['sha'] = 'a'*40
        result = discover(self.config, self.state, self.remote)
        self.assertEqual(result['invalid'], 1); self.assertEqual(valid_days(self.state)['days'], [])
